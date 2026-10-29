"""主流程集成测试：真实配置加载、checker、进程锁和 SQLite，替换 HTTP/SMTP。

fixture 是 pytest 自动准备并传入测试的前置资源；tmp_path 为每个测试新建目录，
monkeypatch 在测试结束后恢复替换对象。测试只读示例，从不读取真实 config.yaml。
阅读顺序：辅助函数 → fixture → 正常流程 → 错误、恢复与子进程。
"""

from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
import sqlite3
import subprocess
import sys
from types import SimpleNamespace

import httpx
import pytest
import yaml

from website_healthcheck import main as entry
from website_healthcheck.checker import checker
from website_healthcheck.config.load_config import load_config
from website_healthcheck.notifier.notifier import MailError
from website_healthcheck.scheduler.process_lock import LockBusyError, process_lock
from website_healthcheck.storage.storage import Storage, StorageError

EXAMPLE = Path(__file__).resolve().parents[1] / "src/website_healthcheck/config/config.yaml.example"
NOW = datetime(2026, 10, 18, tzinfo=UTC)
SECRET = "main-test-only-password"


def rows(database, sql):
    """通过独立连接读取已提交内容，不依赖 Storage 的私有连接。"""
    with closing(sqlite3.connect(database)) as connection:
        connection.row_factory = sqlite3.Row
        return connection.execute(sql).fetchall()


@pytest.fixture
def config_file(tmp_path):
    """由无凭证示例生成临时配置；相对 var 路径也落在临时目录。"""
    data = yaml.safe_load(EXAMPLE.read_text())
    data["targets"] = ["https://example.com/", "https://example.com/playbook/"]
    data["check"]["retries"] = 0
    data["mail"]["password"] = SECRET
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


@pytest.fixture
def clock(monkeypatch):
    """冻结 UTC 时间；修改 value 代替真实等待五分钟。"""
    clock = SimpleNamespace(value=NOW)

    class Clock:
        @classmethod
        def now(cls, timezone):
            return clock.value.astimezone(timezone)

    monkeypatch.setattr(entry, "datetime", Clock)
    monkeypatch.setattr(checker, "datetime", Clock)
    monkeypatch.setattr(checker.time, "sleep", lambda seconds: None)
    return clock


@pytest.fixture
def services(monkeypatch, config_file, clock):
    """记录请求与报告；在 SMTP 替身里检查运行已完成、锁尚未释放。"""
    state = SimpleNamespace(status=200, requests=[], reports=[], mail_error=False)
    config = load_config(config_file)
    real_client = httpx.Client

    def respond(request):
        # 连页面请求期间也必须持锁，不能只在写数据库或发邮件时加锁。
        with pytest.raises(LockBusyError):
            with process_lock(config.storage.lock_path):
                pytest.fail("HTTP 检查期间必须独占锁")
        state.requests.append(str(request.url))
        return httpx.Response(state.status, text="Canlah")

    def send(report, settings):
        assert rows(config.storage.database_path, "SELECT status FROM runs ORDER BY id")[-1][0] == "completed"
        with pytest.raises(LockBusyError):
            with process_lock(config.storage.lock_path):
                pytest.fail("SMTP 尚未结束就释放了锁")
        state.reports.append(report)
        if state.mail_error:
            raise MailError("测试 SMTP 失败")

    monkeypatch.setattr(entry.httpx, "Client", lambda: real_client(
        transport=httpx.MockTransport(respond), trust_env=False,
    ))
    monkeypatch.setattr(entry, "send_alert", send)
    return state


def test_healthy_run(config_file, services, capsys):
    """两个页面都完成且持久化；无事件不发空邮件，返回 0。"""
    assert entry.main(["--config", str(config_file)]) == 0
    database = load_config(config_file).storage.database_path
    assert len(services.requests) == 2
    assert services.reports == []
    assert [r[0] for r in rows(database, "SELECT status FROM runs")] == ["completed"]
    assert len(rows(database, "SELECT * FROM checks")) == 2
    assert rows(database, "SELECT * FROM deliveries") == []
    assert "0 个最终失败" in capsys.readouterr().out


def test_final_failure_sends_after_save(config_file, services):
    """网站故障返回 1；completed 与成功通知可以同时成立。"""
    services.status = 503
    assert entry.main(["--config", str(config_file)]) == 1
    assert len(services.reports) == 1
    assert len(services.reports[0].failed_checks) == 2
    database = load_config(config_file).storage.database_path
    assert rows(database, "SELECT outcome FROM deliveries")[0][0] == "success"
    assert all(r[0] for r in rows(database, "SELECT notified_at FROM events"))


def test_mail_error_priority_and_retry_interval(config_file, services, clock, capsys):
    """SMTP 错误优先于网站故障；失败保留，恢复后按独立邮件间隔汇总。"""
    data = yaml.safe_load(config_file.read_text())
    data["mail"]["interval_minutes"] = 10  # 检查周期仍是 5 分钟。
    config_file.write_text(yaml.safe_dump(data))
    database = load_config(config_file).storage.database_path
    services.status, services.mail_error = 503, True
    assert entry.main(["--config", str(config_file)]) == 2
    assert "SMTP" in capsys.readouterr().err
    assert all(r[0] is None for r in rows(database, "SELECT notified_at FROM events"))

    clock.value += timedelta(minutes=5)
    services.status, services.mail_error = 200, False
    assert entry.main(["--config", str(config_file)]) == 0
    assert len(services.reports) == 1  # 未满邮件间隔，不再发信。

    clock.value += timedelta(minutes=5)
    assert entry.main(["--config", str(config_file)]) == 0
    report = services.reports[-1]
    assert len(services.reports) == 2
    assert len(report.failed_checks) == 2
    assert all(check.result.success for check in report.latest_checks)
    assert [r[0] for r in rows(database, "SELECT outcome FROM deliveries ORDER BY id")] == ["failed", "success"]
    assert all(r[0] for r in rows(database, "SELECT notified_at FROM events"))


def test_nested_call_does_not_stop_itself(config_file, services, capsys):
    """同一进程中的重复调用不能终止自己；返回 3，且不触发业务操作。"""
    config = load_config(config_file)
    with process_lock(config.storage.lock_path):
        assert entry.main(["--config", str(config_file)]) == 3
    assert not config.storage.database_path.exists()
    assert services.requests == services.reports == []
    assert "不能终止当前进程" in capsys.readouterr().err


def test_same_slot_skips_second_check(config_file, services):
    """锁已释放但同一时段有记录，唯一约束仍阻止重复检查。"""
    assert entry.main(["--config", str(config_file)]) == 0
    assert entry.main(["--config", str(config_file)]) == 3
    assert len(services.requests) == 2
    database = load_config(config_file).storage.database_path
    assert len(rows(database, "SELECT * FROM runs")) == 1


def test_interrupted_run_recovers_next_time(config_file, services, clock, monkeypatch, capsys):
    """异常不泄露原文，保留 running；下次恢复为 interrupted，不补跑。"""
    def crash(config, client):
        raise RuntimeError(SECRET)

    with monkeypatch.context() as patch:
        patch.setattr(entry, "run_checks", crash)
        assert entry.main(["--config", str(config_file)]) == 2
    assert SECRET not in capsys.readouterr().err
    database = load_config(config_file).storage.database_path
    assert rows(database, "SELECT status FROM runs")[0][0] == "running"
    clock.value += timedelta(minutes=5)
    assert entry.main(["--config", str(config_file)]) == 0
    assert [r[0] for r in rows(database, "SELECT status FROM runs ORDER BY id")] == ["interrupted", "completed"]
    assert [event.kind for event in services.reports[0].events] == ["interrupted"]
    assert len(services.requests) == 2


def test_missed_slots_report_without_backfill(config_file, services, clock):
    """停机后只请求当前轮两个页面，历史缺口只作为事件汇总。"""
    assert entry.main(["--config", str(config_file)]) == 0
    clock.value += timedelta(minutes=15)
    assert entry.main(["--config", str(config_file)]) == 0
    assert len(services.requests) == 4
    event = services.reports[0].events[0]
    assert event.kind == "missed"
    assert (event.range_start, event.range_end) == (NOW + timedelta(minutes=5), NOW + timedelta(minutes=10))


@pytest.mark.parametrize("method", ["reconcile_schedule", "start_run", "finish_run", "pending_report", "record_delivery"])
def test_storage_errors_are_execution_errors(method, config_file, services, monkeypatch, capsys):
    """不同阶段的存储异常均返回 2；结束后须释放锁以便下一轮恢复。"""
    def fail(*args, **kwargs):
        raise StorageError("测试存储失败")

    services.status = 503  # 也会进入通知分支。
    monkeypatch.setattr(Storage, method, fail)
    assert entry.main(["--config", str(config_file)]) == 2
    assert "测试存储失败" in capsys.readouterr().err
    with process_lock(load_config(config_file).storage.lock_path):
        pass


def test_lock_path_error(config_file, services, capsys):
    """锁路径实际为目录时，返回执行错误 2，不建数据库或请求页面。"""
    config = load_config(config_file)
    config.storage.lock_path.mkdir(parents=True)
    assert entry.main(["--config", str(config_file)]) == 2
    assert "进程锁" in capsys.readouterr().err
    assert services.requests == []
    assert not config.storage.database_path.exists()


def test_database_error(config_file, services):
    """数据库路径实际为目录时，初始化失败也会释放锁。"""
    config = load_config(config_file)
    config.storage.database_path.mkdir(parents=True)
    assert entry.main(["--config", str(config_file)]) == 2
    assert services.requests == []
    with process_lock(config.storage.lock_path):
        pass


def test_missing_config(tmp_path, capsys):
    """配置错误先于所有副作用；不读取包内真实配置。"""
    assert entry.main(["--config", str(tmp_path / "missing.yaml")]) == 2
    assert "不存在" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


def test_default_config_location():
    """仅检查默认路径，不读取文件。"""
    assert entry.DEFAULT_CONFIG == EXAMPLE.with_name("config.yaml")


def test_installed_help_uses_new_name_without_running_checks(tmp_path):
    """从空目录调用真实新命令的帮助入口；无需配置，也不生成运行文件。"""
    executable = Path(sys.executable).parent / "website-healthcheck"
    result = subprocess.run(
        [str(executable), "--help"], cwd=tmp_path,
        capture_output=True, text=True, check=False, timeout=10,
    )
    assert result.returncode == 0
    assert "usage: website-healthcheck" in result.stdout
    assert "--config" in result.stdout
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("busy, expected", [(False, 2), (True, 3)])
def test_installed_entrypoint_from_other_directory(busy, expected, config_file, tmp_path):
    """真实子进程验证入口、跨目录和父辈保护；子进程不能终止持锁的测试父进程，不走 HTTP。"""
    executable = Path(sys.executable).parent / "website-healthcheck"
    config = load_config(config_file)
    if busy:
        with process_lock(config.storage.lock_path):
            result = subprocess.run(
                [str(executable), "--config", str(config_file)],
                cwd=tmp_path, capture_output=True, text=True, check=False, timeout=10,
            )
    else:
        result = subprocess.run(
            [str(executable), "--config", str(tmp_path / "missing.yaml")],
            cwd=tmp_path, capture_output=True, text=True, check=False, timeout=10,
        )
    assert result.returncode == expected
    assert SECRET not in result.stdout + result.stderr
    assert not config.storage.database_path.exists()


def test_start_slot_is_recomputed_after_waiting(config_file, services, clock, monkeypatch):
    """等待旧实例停止时若跨过五分钟边界，执行新当前时段，不执行已过的旧时段。"""
    from contextlib import contextmanager
    import json

    assert entry.main(["--config", str(config_file)]) == 0
    clock.value = NOW + timedelta(minutes=9, seconds=59)

    @contextmanager
    def delayed_lock(path, **kwargs):
        with process_lock(path, **kwargs) as execution:
            clock.value += timedelta(seconds=2)
            yield execution

    monkeypatch.setattr(entry, "process_lock", delayed_lock)
    assert entry.main(["--config", str(config_file)]) == 0
    config = load_config(config_file)
    slots = rows(config.storage.database_path, "SELECT slot FROM runs ORDER BY id")
    assert datetime.fromisoformat(slots[-1][0]) == NOW + timedelta(minutes=10)
    assert services.reports[0].events[0].range_start == NOW + timedelta(minutes=5)
    assert services.reports[0].events[0].range_end == NOW + timedelta(minutes=5)
    metadata = json.loads(config.storage.lock_path.with_name(config.storage.lock_path.name + ".owner.json").read_text())
    assert datetime.fromisoformat(metadata["slot"]) == NOW + timedelta(minutes=10)
