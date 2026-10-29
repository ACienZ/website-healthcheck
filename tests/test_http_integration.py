"""本机 HTTP 集成测试：真正建立连接，不使用 HTTPX MockTransport。

服务只监听 127.0.0.1，由操作系统分配空闲端口；网站响应完全由测试控制。
主流程使用临时配置、真实 SQLite 和锁，仅把 SMTP 连接替换为记录邮件的对象。
因此它能验证各模块配合，却不代表 canlah.ai 可访问或 Gmail 已实际送达。
"""

from collections import Counter
from contextlib import closing
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import smtplib
import socket
import sqlite3
import subprocess
import sys
from threading import Thread
import time
from types import SimpleNamespace

import httpx
import pytest
import yaml

from website_healthcheck import main as entry
from website_healthcheck.checker.checker import check_page
from website_healthcheck.config.load_config import CheckSettings, load_config
from website_healthcheck.notifier import notifier

EXAMPLE = Path(__file__).resolve().parents[1] / "src/website_healthcheck/config/config.yaml.example"
SECRET = "http-integration-test-password"


@pytest.fixture
def http_site():
    """启动可控的测试网站；yield 把地址交给测试，之后关闭服务并回收线程。

    不固定端口，避免和开发者自己的服务冲突。延迟发生在响应头之后，
    用来区分“只收到头部”与“完整读完正文”，而不是模拟一个计时数字。
    """
    requests = Counter()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests[self.path] += 1
            status, body = 200, b"Canlah"
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "/healthy")
                self.end_headers()
                return
            if self.path == "/lowercase":
                body = b"canlah"
            elif self.path == "/unavailable":
                status = 503
            elif self.path == "/recover" and requests[self.path] == 1:
                status = 503

            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            # 声明的长度比实际发送的长，构造传输中途断开的协议错误。
            self.send_header("Content-Length", str(len(body) + (10 if self.path == "/truncated" else 0)))
            self.end_headers()
            try:
                self.wfile.write(body[:3])
                self.wfile.flush()
                if self.path == "/slow-body":
                    time.sleep(0.25)
                elif self.path == "/read-timeout":
                    time.sleep(0.6)
                self.wfile.write(body[3:])
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                # 超时测试中，客户端会先断开；这正是测试预期，不作为服务异常打印。
                pass

        def log_message(self, format, *args):
            # 不把每条测试请求刷到终端；断言直接检查请求计数和返回结果。
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        # 关闭服务时也等仍在发送正文的请求线程结束，不把后台线程留给下一个测试。
        server.daemon_threads = False
        thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
        thread.start()
        try:
            yield SimpleNamespace(
                url=f"http://127.0.0.1:{server.server_port}", requests=requests,
            )
        finally:
            # 断言失败也会执行 finally，避免测试结束后服务继续监听。
            server.shutdown()
            thread.join(timeout=5)


@pytest.mark.parametrize("path,failures", [
    ("/healthy", ()),
    ("/redirect", ()),
    ("/lowercase", ("keyword",)),
    ("/unavailable", ("status",)),
    ("/truncated", ("network",)),
])
def test_actual_http_response(http_site, path, failures):
    """用真实连接检查正文、大小写、状态和读取中断；重定向必须实际到达下一页。"""
    with httpx.Client(trust_env=False) as client:
        result = check_page(http_site.url + path, CheckSettings(retries=0), client)
    attempt = result.attempts[0]
    assert attempt.failure_codes == failures
    assert result.success is (not failures)
    assert http_site.requests[path] == 1
    if path == "/redirect":
        assert http_site.requests["/healthy"] == 1
        assert attempt.final_url == http_site.url + "/healthy"
        assert attempt.status_code == 200
    if path == "/truncated":
        assert attempt.keyword_found is None  # 不能拿未读完的正文判断关键词。


def test_actual_body_delay_counts_toward_latency(http_site):
    """正文分两次发完；第二段到达后才检查，所以必须计入中间真实等待的时间。"""
    settings = CheckSettings(retries=0, max_response_seconds=0.05, request_timeout_seconds=2)
    with httpx.Client(trust_env=False) as client:
        attempt = check_page(http_site.url + "/slow-body", settings, client).attempts[0]
    assert attempt.elapsed_seconds >= 0.25
    assert attempt.keyword_found is True  # Can 与 lah 分开发送，最终要拼成 Canlah。
    assert attempt.failure_codes == ("latency",)


def test_actual_read_timeout(http_site):
    """收到部分正文后迟迟没有后续数据，HTTPX 必须产生真实读取超时。"""
    settings = CheckSettings(retries=0, request_timeout_seconds=0.1)
    with httpx.Client(trust_env=False) as client:
        attempt = check_page(http_site.url + "/read-timeout", settings, client).attempts[0]
    assert attempt.failure_codes == ("timeout",)
    assert attempt.keyword_found is None
    assert http_site.requests["/read-timeout"] == 1


def test_actual_unavailable_port():
    """端口被占用但没有服务监听，实际请求应失败，且不能伪造响应数据。

    始终占住端口，避免先寻找空闲端口再关闭时，被别的程序抢先拿走。
    系统可能立即拒绝，也可能等到连接超时；两种都属于合理的网络失败。
    """
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        target = f"http://127.0.0.1:{reserved.getsockname()[1]}/"
        with httpx.Client(trust_env=False) as client:
            settings = CheckSettings(retries=0, request_timeout_seconds=0.2)
            attempt = check_page(target, settings, client).attempts[0]
    assert attempt.failure_codes in (("network",), ("timeout",))
    assert attempt.status_code is None
    assert attempt.keyword_found is None


@pytest.mark.parametrize("path,statuses", [
    ("/recover", [503, 200]),
    ("/unavailable", [503, 503, 503]),
])
def test_actual_retry_requests(http_site, path, statuses):
    """服务端实际收到 2 或 3 次请求；恢复后立即停止，不再用完剩余次数。"""
    settings = CheckSettings(retries=2, retry_delay_seconds=0)
    with httpx.Client(trust_env=False) as client:
        result = check_page(http_site.url + path, settings, client)
    assert [attempt.status_code for attempt in result.attempts] == statuses
    assert http_site.requests[path] == len(statuses)
    assert result.success is (statuses[-1] == 200)


@pytest.fixture
def config_file(tmp_path, http_site, monkeypatch):
    """复制无凭证示例，把目标换成本机；配置与数据库都只写入 tmp_path。

    主程序和子进程保留真正的 HTTPX Client；NO_PROXY 让回环请求不受本机代理影响。
    """
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost,::1")
    data = yaml.safe_load(EXAMPLE.read_text())
    data["targets"] = [http_site.url + "/recover", http_site.url + "/healthy"]
    data["check"]["retry_delay_seconds"] = 0
    data["mail"]["password"] = SECRET
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


def rows(database, sql):
    """从另一个 SQLite 连接读取，确保检查的是落盘结果而不是内存中的对象。"""
    with closing(sqlite3.connect(database)) as connection:
        return connection.execute(sql).fetchall()


@pytest.fixture
def smtp(monkeypatch):
    """只替换网络出口 SMTP_SSL；保留真正的报告生成、邮件组装和通知状态处理。"""
    state = SimpleNamespace(messages=[], fail=False)

    class SMTP:
        def __init__(self, host, port, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def login(self, username, password):
            assert password == SECRET

        def send_message(self, message, **kwargs):
            state.messages.append(message)
            if state.fail:
                raise smtplib.SMTPException(SECRET)
            return {}

    monkeypatch.setattr(notifier.smtplib, "SMTP_SSL", SMTP)
    return state


@pytest.fixture
def clock(monkeypatch):
    """只推进主流程的日历时间，不替换 HTTP 的计时和等待，也不真的等五分钟。"""
    state = SimpleNamespace(value=datetime(2026, 10, 18, tzinfo=UTC))

    class Clock:
        @classmethod
        def now(cls, timezone):
            return state.value.astimezone(timezone)

    monkeypatch.setattr(entry, "datetime", Clock)
    return state


def test_http_recovery_persists_all_attempts_without_alert(config_file, http_site, smtp, clock):
    """实际重试后恢复：保存三次请求明细，但不产生最终故障事件或邮件。"""
    assert entry.main(["--config", str(config_file)]) == 0
    database = load_config(config_file).storage.database_path
    assert http_site.requests == {"/recover": 2, "/healthy": 1}
    assert rows(database, "SELECT status FROM runs") == [("completed",)]
    assert rows(database, "SELECT status_code FROM attempts ORDER BY id") == [(503,), (200,), (200,)]
    assert rows(database, "SELECT * FROM events") == []
    assert smtp.messages == []


@pytest.mark.parametrize("mail_fails,exit_code,outcome", [(False, 1, "success"), (True, 2, "failed")])
def test_http_failure_through_email_and_storage(
    config_file, http_site, smtp, clock, capsys, mail_fails, exit_code, outcome,
):
    """故障耗尽后继续检查第二页；真正生成邮件，成功才标记，SMTP 错误不泄露密码。"""
    data = yaml.safe_load(config_file.read_text())
    data["targets"][0] = http_site.url + "/unavailable"
    config_file.write_text(yaml.safe_dump(data))
    smtp.fail = mail_fails
    assert entry.main(["--config", str(config_file)]) == exit_code
    database = load_config(config_file).storage.database_path
    assert http_site.requests == {"/unavailable": 3, "/healthy": 1}
    assert rows(database, "SELECT success FROM checks ORDER BY id") == [(0,), (1,)]
    assert rows(database, "SELECT outcome FROM deliveries") == [(outcome,)]
    assert len(smtp.messages) == 1
    message = smtp.messages[0]
    assert "实际重试：2 次" in message.get_content()
    assert http_site.url + "/healthy" in message.get_content()
    assert all((value is None) is mail_fails for value, in rows(database, "SELECT notified_at FROM events"))
    captured = capsys.readouterr()
    assert SECRET not in message.as_string() + captured.out + captured.err

    # 五分钟后将目标恢复为健康页面。成功发送过就不重发；之前失败的邮件仍应补通知。
    clock.value += timedelta(minutes=5)
    data["targets"][0] = http_site.url + "/healthy?recovered"
    config_file.write_text(yaml.safe_dump(data))
    smtp.fail = False
    assert entry.main(["--config", str(config_file)]) == 0
    assert len(smtp.messages) == (2 if mail_fails else 1)
    assert all(value is not None for value, in rows(database, "SELECT notified_at FROM events"))
    if mail_fails:
        assert "历史故障详情" in smtp.messages[-1].get_content()
        assert http_site.url + "/healthy?recovered" in smtp.messages[-1].get_content()


def test_installed_command_with_actual_http(config_file, tmp_path, http_site):
    """从别的目录启动已安装命令，实际请求并写库；同一时段再调用返回 3。

    两个目标都健康，因此子进程不会进入 SMTP；它没有继承上面的 SMTP 替身。
    第二次调用固定为刚写入的时段，避免整点跨时段影响重复检测。
    """
    data = yaml.safe_load(config_file.read_text())
    data["targets"] = [http_site.url + "/healthy", http_site.url + "/redirect"]
    data["schedule"]["interval_minutes"] = 60
    config_file.write_text(yaml.safe_dump(data))
    command = [str(Path(sys.executable).parent / "website-healthcheck"), "--config", str(config_file)]
    result = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    database = load_config(config_file).storage.database_path
    assert rows(database, "SELECT status FROM runs") == [("completed",)]
    assert len(rows(database, "SELECT * FROM checks")) == 2
    assert rows(database, "SELECT * FROM deliveries") == []
    requests_before = http_site.requests.copy()
    # 使用数据库刚写入的时段调用主流程，避免这一步恰逢整点造成偶发失败。
    slot = datetime.fromisoformat(rows(database, "SELECT slot FROM runs")[0][0])

    class SameSlotClock:
        @classmethod
        def now(cls, timezone):
            return slot.astimezone(timezone)

    # 只给第二次调用固定时段；首次运行仍是未替换代码的真实命令子进程。
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(entry, "datetime", SameSlotClock)
        assert entry.main(["--config", str(config_file)]) == 3
    assert http_site.requests == requests_before
