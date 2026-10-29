"""邮件内容、SMTP 边界和存储协作测试；不连接 Gmail，不读取真实配置。

运行：uv run --locked pytest tests/test_notifier.py -v
fixture 准备每项测试独立的数据；monkeypatch 将 SMTP_SSL 换成内存替身。
临时 SQLite 测试按未来主程序的调用顺序验证接口，主程序本身仍在步骤 5 接入。
"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from email import policy
from email.parser import BytesParser
import smtplib
import ssl
import traceback

import pytest

from website_healthcheck.checker.checker import Attempt, CheckResult
from website_healthcheck.config.load_config import MailSettings, ScheduleSettings
from website_healthcheck.notifier import notifier
from website_healthcheck.storage.models import Event, Report, StoredCheck
from website_healthcheck.storage.storage import Storage

NOW = datetime(2026, 10, 18, tzinfo=UTC)
SECRET = "test-only-mail-password"  # 人工测试值，不是真实账号密码。


def check_result(codes=(), *, target="https://example.com/", attempts=1):
    """构造检查结果，不需要 HTTP 请求；最后一次的原因决定是否健康。"""
    return CheckResult(target, tuple(
        Attempt(
            number=number, started_at=NOW + timedelta(seconds=number),
            elapsed_seconds=0.25, final_url=target, status_code=200,
            keyword_found="keyword" not in codes, failure_codes=codes,
        ) for number in range(1, attempts + 1)
    ))


@pytest.fixture
def settings():
    """用合法假地址直接构造配置，避免接触包内真实 config.yaml。"""
    return MailSettings(
        username="test-sender@gmail.com", password=SECRET,
        sender="test-sender@gmail.com",
        recipients=["first@example.com", "second@example.com"],
    )


@pytest.fixture
def report():
    """同一目标先故障后恢复；同时有漏跑和中断，验证一封邮件能完整汇总。"""
    failed = StoredCheck(11, 1, NOW + timedelta(seconds=30), check_result(
        ("status", "latency", "keyword"), attempts=3,
    ))
    latest = StoredCheck(12, 2, NOW + timedelta(minutes=5, seconds=30), check_result())
    return Report(
        events=(
            Event(1, "check_failed", failed.finished_at, 1, 11, None, None),
            Event(2, "missed", latest.finished_at, None, None, NOW, NOW + timedelta(minutes=5)),
            Event(3, "interrupted", latest.finished_at, 3, None, None, None),
        ),
        failed_checks=(failed,), latest_checks=(latest,),
        period_start=NOW, period_end=latest.finished_at,
    )


@pytest.fixture
def smtp(monkeypatch):
    """替身记录连接、认证、邮件和退出；可在指定阶段模拟失败。

    class 只是测试用的 SMTP 上下文对象，不实现网络协议。monkeypatch 在测试结束后
    自动恢复原 SMTP_SSL；每项测试新建替身，状态不会串到下一项。
    """
    class FakeSMTP:
        def __init__(self):
            self.connections = []
            self.logins = []
            self.messages = []
            self.closed = False
            self.stage = None
            self.error = None
            self.refused = {}

        def fail_at(self, stage):
            if self.stage == stage:
                raise self.error

        def connect(self, host, port, *, timeout, context):
            self.connections.append((host, port, timeout, context))
            self.fail_at("connect")
            return self

        def __enter__(self):
            return self

        def login(self, username, password):
            self.logins.append((username, password))
            self.fail_at("login")

        def send_message(self, message, *, from_addr, to_addrs):
            self.messages.append((message, from_addr, list(to_addrs)))
            self.fail_at("send")
            return self.refused

        def __exit__(self, exc_type, exc, tb):
            self.closed = True
            self.fail_at("quit")

    fake = FakeSMTP()
    monkeypatch.setattr(notifier.smtplib, "SMTP_SSL", fake.connect)
    return fake


def test_ssl_login_and_message_content(report, settings, smtp, capsys):
    notifier.send_alert(report, settings)
    assert len(smtp.connections) == 1
    host, port, timeout, context = smtp.connections[0]
    assert (host, port, timeout) == (settings.host, settings.port, settings.timeout_seconds)
    assert context.check_hostname is True
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert smtp.logins == [(settings.username, SECRET)]
    assert smtp.closed is True
    message, sender, recipients = smtp.messages[0]
    assert sender == settings.sender
    assert recipients == settings.recipients
    # bytes 往返解析，验证实际 MIME 编码后仍能读取中文、收件地址和正文。
    parsed = BytesParser(policy=policy.default).parsebytes(message.as_bytes())
    assert str(parsed["Subject"]) == "[Website Healthcheck] 3 条待通知事件"
    assert str(parsed["From"]) == settings.sender
    assert {address.addr_spec for address in parsed["To"].addresses} == set(settings.recipients)
    assert parsed["Date"].datetime == report.period_end
    assert parsed.get_content_type() == "text/plain"
    body = parsed.get_content()
    assert "2026-10-18 00:00:00 UTC 至 2026-10-18 00:05:30 UTC" in body
    assert "页面最终失败，检查 #11 / 运行 #1" in body
    assert "漏跑时段：2026-10-18 00:00:00 UTC 至 2026-10-18 00:05:00 UTC（含首尾；不补跑）" in body
    assert "运行 #3 异常中断" in body
    assert "发现时间不代表实际中断时间" in body
    assert "最终状态：故障；实际重试：2 次" in body
    assert "最终状态：正常；实际重试：0 次" in body
    assert all(f"尝试 {number}" in body for number in (1, 2, 3))
    assert "状态码不符合预期、完整响应耗时超标、缺少关键词" in body
    assert "最终 URL：https://example.com/" in body
    assert SECRET not in message.as_string()
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("code,label", [("timeout", "请求超时"), ("network", "网络错误")])
def test_no_response_metadata(report, settings, smtp, code, label):
    attempt = replace(
        report.failed_checks[0].result.attempts[0], final_url=None,
        status_code=None, keyword_found=None, failure_codes=(code,),
    )
    failed = replace(report.failed_checks[0], result=CheckResult("https://example.com/", (attempt,)))
    notifier.send_alert(replace(report, failed_checks=(failed,), latest_checks=()), settings)
    body = smtp.messages[0][0].get_content()
    assert "状态码 未取得" in body
    assert "关键词 未判断" in body
    assert f"失败原因 {label}" in body
    assert "最终 URL" not in body
    assert "暂无已完成检查" in body


def test_only_schedule_events_without_completed_checks(report, settings, smtp):
    notifier.send_alert(replace(report, events=report.events[1:], failed_checks=(), latest_checks=()), settings)
    body = smtp.messages[0][0].get_content()
    assert "无页面最终失败记录" in body
    assert "不能据此判断网站是否健康" in body


def test_empty_report_does_not_connect(report, settings, smtp):
    notifier.send_alert(replace(report, events=(), failed_checks=(), latest_checks=()), settings)
    assert smtp.connections == []


def test_connection_uses_configured_values(report, settings, smtp):
    custom = settings.model_copy(update={
        "host": "smtp.example.com", "port": 8465, "timeout_seconds": 3.5,
        "recipients": ["only@example.com"],
    })
    notifier.send_alert(report, custom)
    assert smtp.connections[0][:3] == ("smtp.example.com", 8465, 3.5)
    assert smtp.messages[0][2] == ["only@example.com"]


# parametrize 将一项规则展开为多个独立用例，逐个覆盖连接、认证、发送及关闭失败。
@pytest.mark.parametrize("stage", ["connect", "login", "send", "quit"])
@pytest.mark.parametrize("error", [
    smtplib.SMTPException(SECRET), OSError(SECRET),
])
def test_errors_are_redacted_without_internal_retry(report, settings, smtp, capsys, stage, error):
    smtp.stage, smtp.error = stage, error
    with pytest.raises(notifier.MailError) as captured:
        notifier.send_alert(report, settings)
    # 验证默认 traceback 也不显示原始异常，而不只是检查 str(exception)。
    displayed = "".join(traceback.format_exception(captured.value))
    assert SECRET not in displayed
    assert len(smtp.connections) == 1  # 不在一次调用里连续重试 Gmail。
    if stage != "connect":
        assert smtp.closed
    assert capsys.readouterr() == ("", "")


def test_partial_recipient_refusal_is_failure(report, settings, smtp):
    # 标准库部分拒收时返回非空字典；不能误报为整批投递成功。
    smtp.refused = {settings.recipients[1]: (550, SECRET.encode())}
    with pytest.raises(notifier.MailError, match="未接受全部收件人") as captured:
        notifier.send_alert(report, settings)
    assert SECRET not in str(captured.value)
    assert smtp.closed


def test_full_recipient_refusal_is_redacted(report, settings, smtp):
    smtp.stage = "send"
    smtp.error = smtplib.SMTPRecipientsRefused({settings.recipients[0]: (550, SECRET.encode())})
    with pytest.raises(notifier.MailError) as captured:
        notifier.send_alert(report, settings)
    assert SECRET not in "".join(traceback.format_exception(captured.value))


def test_header_error_is_redacted(report, settings, smtp):
    # model_copy 不重新验证，仅在测试中用它模拟绕过配置加载器的非法邮件头。
    invalid = settings.model_copy(update={"sender": f"test@example.com\n{SECRET}"})
    with pytest.raises(notifier.MailError) as captured:
        notifier.send_alert(report, invalid)
    assert SECRET not in "".join(traceback.format_exception(captured.value))
    assert smtp.connections == []


def test_mail_interval_and_delivery_state(tmp_path, settings, smtp):
    """真实临时库 + 假 SMTP：失败保留、到期重试、恢复后仍汇报历史、成功再标记。"""
    with Storage(tmp_path / "test.sqlite3") as store:
        assert store.pending_report(NOW, settings.interval_minutes) is None
        assert smtp.connections == []
        run = store.start_run(NOW, NOW)
        store.finish_run(run, [check_result(("keyword",))], NOW + timedelta(seconds=30))
        attempted = NOW + timedelta(seconds=31)
        report = store.pending_report(attempted, settings.interval_minutes)
        smtp.stage, smtp.error = "login", smtplib.SMTPAuthenticationError(535, SECRET.encode())
        with pytest.raises(notifier.MailError):
            notifier.send_alert(report, settings)
        store.record_delivery(report, "failed", attempted)

        # 4:59 时事件仍在，但间隔未到，不调用 send_alert，故没有第二次连接。
        assert store.pending_report(attempted + timedelta(minutes=5, seconds=-1), settings.interval_minutes) is None
        assert len(smtp.connections) == 1
        next_slot = NOW + timedelta(minutes=5)
        run = store.start_run(next_slot, next_slot)
        store.finish_run(run, [check_result()], next_slot + timedelta(seconds=30))
        retry_at = attempted + timedelta(minutes=5)
        retry_report = store.pending_report(retry_at, settings.interval_minutes)
        assert retry_report.event_ids == report.event_ids
        assert retry_report.latest_checks[0].result.success
        smtp.stage = None
        notifier.send_alert(retry_report, settings)
        body = smtp.messages[0][0].get_content()
        assert "最终状态：故障" in body and "最终状态：正常" in body
        # send_alert 自身不修改数据库，只有调用方明确记录成功才消除待通知状态。
        assert store.pending_report(retry_at, settings.interval_minutes) is not None
        store.record_delivery(retry_report, "success", retry_at)
        assert store.pending_report(retry_at + timedelta(minutes=5), settings.interval_minutes) is None


def test_sending_snapshot_does_not_mark_new_event(tmp_path, settings, smtp):
    """报告生成之后新出现的事件不属于旧批次，成功记录只能标记旧事件。"""
    with Storage(tmp_path / "test.sqlite3") as store:
        store.reconcile_schedule(NOW, ScheduleSettings())
        run = store.start_run(NOW, NOW)
        store.finish_run(run, [check_result(("keyword",))], NOW + timedelta(seconds=30))
        report = store.pending_report(NOW + timedelta(seconds=30), settings.interval_minutes)
        later = NOW + timedelta(minutes=15)
        store.reconcile_schedule(later, ScheduleSettings())
        notifier.send_alert(report, settings)
        store.record_delivery(report, "success", later)
        pending = store.pending_report(later + timedelta(minutes=5), settings.interval_minutes)
        assert pending is not None
        assert set(pending.event_ids).isdisjoint(report.event_ids)
        assert [event.kind for event in pending.events] == ["missed"]
