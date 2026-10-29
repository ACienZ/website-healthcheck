"""用真实子进程验证“停止旧实例再执行新一轮”，只终止本测试创建的子进程。

就绪消息由管道传递并设超时，不靠 sleep 猜进程是否已持锁。
HTTP、SMTP 不联网；接替后的数据库操作使用真实 Storage 和主入口。
"""

from contextlib import closing
from datetime import UTC, datetime, timedelta
import errno
import json
from pathlib import Path
import select
import signal
import sqlite3
import subprocess
import sys

import httpx
import psutil
import pytest
import yaml

from website_healthcheck import main as entry
from website_healthcheck.checker import checker
from website_healthcheck.scheduler import process_lock as locks

NOW = datetime(2026, 10, 18, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[1]

# 每个子进程先持锁、可选写入运行记录，然后报告 ready，等父测试允许退出。
# ignore 模式故意不响应 SIGTERM，用来验证 SIGKILL 分支；unlocked 模式不持锁。
CHILD = '''
from datetime import datetime, timedelta
from pathlib import Path
import signal
import sys
from website_healthcheck.scheduler.process_lock import process_lock
from website_healthcheck.storage.storage import Storage
from website_healthcheck.config.load_config import ScheduleSettings
from website_healthcheck.checker.checker import Attempt, CheckResult
path, slot, mode, database = sys.argv[1:]
slot = datetime.fromisoformat(slot)
if mode == "unlocked":
    print("ready", flush=True)
    sys.stdin.readline()
    raise SystemExit(0)
if mode == "ignore":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
with process_lock(Path(path), slot=slot) as execution:
    if database:
        with Storage(Path(database)) as store:
            store.reconcile_schedule(slot, ScheduleSettings())
            run_id = store.start_run(slot, slot)
            execution.set_run_id(run_id, slot)
            if mode == "smtp":
                attempt = Attempt(1, slot, 0.1, "https://example.com/", 200, True, ())
                store.finish_run(run_id, [CheckResult("https://example.com/", (attempt,))], slot + timedelta(seconds=1))
    print("ready", flush=True)
    sys.stdin.readline()
'''


@pytest.fixture
def start_owner(tmp_path):
    """所有测试创建的子进程都登记在这里，断言失败也会清理，不影响其他进程。"""
    children = []

    def start(*, mode="normal", database=None, slot=NOW, path=None):
        path = path or tmp_path / "healthcheck.lock"
        process = subprocess.Popen(
            [sys.executable, "-c", CHILD, str(path), slot.isoformat(), mode, str(database) if database else ""],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        children.append(process)
        assert select.select([process.stdout], [], [], 10)[0], "旧实例未及时准备好"
        assert process.stdout.readline().strip() == "ready"
        return process, path

    yield start
    for process in children:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=10)


def owner_file(path):
    return path.with_name(path.name + ".owner.json")


@pytest.mark.parametrize("crash", [False, True])
def test_release_after_exit(tmp_path, crash):
    """正常结束和抛异常都会释放内核锁，磁盘文件不需要删除。"""
    path = tmp_path / "var/healthcheck.lock"
    try:
        with locks.process_lock(path):
            with pytest.raises(locks.LockBusyError, match="当前进程"):
                with locks.process_lock(path):
                    pytest.fail("不能终止自己")
            if crash:
                raise RuntimeError("测试异常")
    except RuntimeError:
        assert crash
    assert path.exists()
    assert json.loads(owner_file(path).read_text())["finished"] == (not crash)
    with locks.process_lock(path):
        pass


@pytest.mark.parametrize("mode, expected_signal", [("normal", signal.SIGTERM), ("ignore", signal.SIGKILL)])
def test_new_slot_stops_previous_owner(start_owner, mode, expected_signal):
    """A 可正常响应终止，也可能卡死不响应；两种情况下 B 都须拿到锁后再执行。"""
    process, path = start_owner(mode=mode)
    with locks.process_lock(path, slot=NOW + timedelta(minutes=5), stop_timeout_seconds=0.2):
        assert process.wait(timeout=5) == -expected_signal
        assert json.loads(owner_file(path).read_text())["pid"] == psutil.Process().pid


def test_same_slot_does_not_stop_owner(start_owner):
    """手动重复启动同一时段不算下一轮，不能终止正在执行的同一轮。"""
    process, path = start_owner()
    with pytest.raises(locks.LockBusyError, match="该时段"):
        with locks.process_lock(path, slot=NOW):
            pytest.fail("重复启动不应执行")
    assert process.poll() is None


def test_normal_completion_is_not_interruption(start_owner):
    """A 恰好先完成时，不需要终止，也不把它误记为中断。"""
    process, path = start_owner()
    process.communicate(input="finish\n", timeout=5)
    assert process.returncode == 0
    with locks.process_lock(path, slot=NOW + timedelta(minutes=5)) as execution:
        assert execution.interrupted_run_id is None


@pytest.mark.parametrize("invalid", ["pid_reused", "wrong_process", "broken_json", "missing_fields"])
def test_invalid_owner_never_gets_a_signal(start_owner, invalid):
    """PID 对不上、指向没打开锁的进程或信息损坏时，不猜测、不误杀。"""
    process, path = start_owner()
    other, unused = start_owner(mode="unlocked")
    metadata = json.loads(owner_file(path).read_text())
    if invalid == "pid_reused":
        metadata["created_at"] -= 100
    elif invalid == "wrong_process":
        metadata["pid"] = other.pid
        metadata["created_at"] = psutil.Process(other.pid).create_time()
    elif invalid == "missing_fields":
        del metadata["slot"]
    owner_file(path).write_text("{" if invalid == "broken_json" else json.dumps(metadata))
    with pytest.raises(locks.LockError):
        with locks.process_lock(path, slot=NOW + timedelta(minutes=5)):
            pytest.fail("无法确认身份时不能开始")
    assert process.poll() is other.poll() is None


def test_failure_to_confirm_stop_does_not_enter_body(start_owner, monkeypatch):
    """等待到上限后仍无法确认锁释放，就报错，而不是让 B 绕过锁执行。"""
    process, path = start_owner(mode="ignore")
    monkeypatch.setattr(locks, "_wait_for_lock", lambda *args: False)
    with pytest.raises(locks.LockError, match="未能停止"):
        with locks.process_lock(path, slot=NOW + timedelta(minutes=5)):
            pytest.fail("未确认旧实例停止时不能执行")
    assert process.wait(timeout=5) == -signal.SIGKILL


def test_permission_error_is_safe(start_owner, monkeypatch):
    """没有权限终止旧实例时返回安全错误，不改变数据库或继续执行。"""
    process, path = start_owner()

    def denied(self):
        raise psutil.AccessDenied(self.pid, msg="test-only-secret")

    monkeypatch.setattr(psutil.Process, "terminate", denied)
    with pytest.raises(locks.LockError, match="无法确认或终止") as error:
        with locks.process_lock(path, slot=NOW + timedelta(minutes=5)):
            pytest.fail("没有终止权限不能并行执行")
    assert "test-only-secret" not in str(error.value)
    assert process.poll() is None


def test_only_one_new_instance_handles_takeover(tmp_path):
    """B 正在处理 A 时，C 不应同时读取并终止进程；短启动锁保护这个步骤。"""
    path = tmp_path / "healthcheck.lock"
    with path.with_name(path.name + ".startup").open("a") as startup:
        assert locks._try_lock(startup)
        with pytest.raises(locks.LockBusyError, match="另一个新实例"):
            with locks.process_lock(path):
                pytest.fail("已有新实例在处理接替")
    assert not owner_file(path).exists()


def test_unexpected_lock_error_is_safe(tmp_path, monkeypatch):
    """磁盘或系统错误不会被当作普通竞争，底层异常文本不向外传。"""
    def fail(*args):
        raise OSError(errno.EIO, "test-only-secret")

    monkeypatch.setattr(locks.fcntl, "flock", fail)
    with pytest.raises(locks.LockError) as error:
        with locks.process_lock(tmp_path / "healthcheck.lock"):
            pytest.fail("锁初始化失败不应执行")
    assert "test-only-secret" not in str(error.value)


@pytest.mark.parametrize("stage", ["ignore", "smtp"])
def test_takeover_records_interruption_and_runs_current_round(start_owner, tmp_path, monkeypatch, stage):
    """A 卡在检查或邮件阶段，B 终止 A、记录中断、完成新一轮，只请求当前目标。

    真实 SQLite 验证状态提交；HTTP 用 MockTransport，发信替身只记录报告。
    两个实例所属时段不同，不真实等五分钟，也不请求外部网络。
    """
    config_path = tmp_path / "config.yaml"
    data = yaml.safe_load((ROOT / "src/website_healthcheck/config/config.yaml.example").read_text())
    data["targets"] = ["https://example.com/"]
    data["schedule"]["stop_timeout_seconds"] = 0.2
    config_path.write_text(yaml.safe_dump(data))
    database, path = tmp_path / "var/healthcheck.sqlite3", tmp_path / "var/healthcheck.lock"
    process, path = start_owner(mode=stage, database=database, path=path)
    requests, reports = [], []
    real_client = httpx.Client

    class Clock:
        @classmethod
        def now(cls, timezone):
            return (NOW + timedelta(minutes=5)).astimezone(timezone)

    def respond(request):
        requests.append(request)
        return httpx.Response(200, text="Canlah")

    monkeypatch.setattr(entry, "datetime", Clock)
    monkeypatch.setattr(checker, "datetime", Clock)
    monkeypatch.setattr(entry.httpx, "Client", lambda: real_client(transport=httpx.MockTransport(respond)))
    monkeypatch.setattr(entry, "send_alert", lambda report, settings: reports.append(report))
    assert entry.main(["--config", str(config_path)]) == 0
    assert process.wait(timeout=5) < 0
    assert len(requests) == 1
    assert [event.kind for event in reports[0].events] == ["interrupted"]
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute("SELECT status FROM runs ORDER BY id").fetchall() == [("interrupted",), ("completed",)]
        assert connection.execute("SELECT COUNT(*) FROM checks").fetchone()[0] == (2 if stage == "smtp" else 1)
        assert connection.execute("SELECT outcome FROM deliveries").fetchall() == [("success",)]


def test_main_does_not_open_database_when_owner_is_unknown(start_owner, tmp_path, monkeypatch, capsys):
    """身份损坏时主入口返回 2，旧实例还活着，B 不建数据库、不请求网站。"""
    path = tmp_path / "var/healthcheck.lock"
    process, path = start_owner(path=path)
    owner_file(path).write_text("{")
    config_path = tmp_path / "config.yaml"
    config_path.write_text((ROOT / "src/website_healthcheck/config/config.yaml.example").read_text())
    monkeypatch.setattr(entry, "run_checks", lambda *args: pytest.fail("不应执行 HTTP"))
    assert entry.main(["--config", str(config_path)]) == 2
    assert "无法确认旧实例身份" in capsys.readouterr().err
    assert not (tmp_path / "var/healthcheck.sqlite3").exists()
    assert process.poll() is None
