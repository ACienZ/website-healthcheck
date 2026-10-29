"""将存储模块的 Report 快照转换成一封纯文本邮件，并通过 SMTP_SSL 发送。

阅读顺序：send_alert → _build_message → _check_lines。
本模块只处理邮件：间隔判断由 Storage.pending_report 完成，发送后的数据库
标记由调用方执行 Storage.record_delivery。这样不会在 SMTP 尚未成功时丢失事件。
"""

from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import format_datetime
import smtplib
import ssl

from website_healthcheck.config.load_config import MailSettings
from website_healthcheck.storage.models import Report, StoredCheck

# 只展示检查模块记录的固定原因，不把服务器响应正文或底层异常装进邮件。
_FAILURE_LABELS = {
    "status": "状态码不符合预期",
    "latency": "完整响应耗时超标",
    "keyword": "缺少关键词",
    "timeout": "请求超时",
    "network": "网络错误",
}


class MailError(RuntimeError):
    """可以向用户展示的发信错误；不包含 SMTP 原始响应或登录凭证。"""


def _time(value: datetime) -> str:
    """所有邮件时间统一展示为 UTC，与数据库的计划时段保持一致。"""
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


def _check_lines(check: StoredCheck) -> list[str]:
    """展示最终判断及每次尝试，区分实际重试次数与配置允许的最大次数。"""
    result = check.result
    lines = [
        f"目标：{result.target}",
        f"检查 #{check.check_id} / 运行 #{check.run_id}；结束：{_time(check.finished_at)}",
        f"最终状态：{'正常' if result.success else '故障'}；实际重试：{len(result.attempts) - 1} 次",
    ]
    for attempt in result.attempts:
        # None 表示没有取得相应数据，不能写成状态码 0 或关键词不匹配。
        status = (
            str(attempt.status_code) if attempt.status_code is not None else "未取得"
        )
        keyword = {True: "存在", False: "缺失", None: "未判断"}[attempt.keyword_found]
        reasons = (
            "、".join(_FAILURE_LABELS[code] for code in attempt.failure_codes) or "无"
        )
        lines.append(
            f"  尝试 {attempt.number}（{_time(attempt.started_at)}）：状态码 {status}；"
            f"完整响应耗时 {attempt.elapsed_seconds:.3f} 秒；关键词 {keyword}；失败原因 {reasons}"
        )
        if attempt.final_url is not None:
            lines.append(f"    最终 URL：{attempt.final_url}")
    return lines


def _build_message(report: Report, settings: MailSettings) -> EmailMessage:
    """根据快照组装邮件；历史故障与最新状态分别展示，避免恢复后掩盖历史问题。"""
    # EmailMessage 负责 MIME 编码及邮件头折行，set_content 默认生成纯文本。
    # 正文只选择报告字段；不序列化 settings，避免密码等连接参数混入邮件。
    message = EmailMessage()
    message["Subject"] = f"[Website Healthcheck] {len(report.events)} 条待通知事件"
    message["From"] = settings.sender
    message["To"] = ", ".join(settings.recipients)
    message["Date"] = format_datetime(report.period_end.astimezone(UTC))
    lines = [
        "Website Healthcheck 汇总告警",
        f"统计区间：{_time(report.period_start)} 至 {_time(report.period_end)}",
        "",
        "待通知事件：",
    ]
    for event in report.events:
        prefix = f"- 事件 #{event.id}；记录/发现时间：{_time(event.occurred_at)}；"
        if event.kind == "check_failed":
            lines.append(
                prefix + f"页面最终失败，检查 #{event.check_id} / 运行 #{event.run_id}"
            )
        elif event.kind == "missed":
            # 存储接口保证漏跑事件的两个端点非空，且首尾均属于漏跑时段。
            lines.append(
                prefix
                + f"漏跑时段：{_time(event.range_start)} 至 {_time(event.range_end)}（含首尾；不补跑）"
            )
        elif event.kind == "interrupted":
            lines.append(
                prefix + f"运行 #{event.run_id} 异常中断；发现时间不代表实际中断时间"
            )

    lines.extend(["", "历史故障详情（即使后来恢复也保留）："])
    for check in report.failed_checks:
        lines.extend(_check_lines(check))
    if not report.failed_checks:
        lines.append("无页面最终失败记录。")
    lines.extend(["", "各目标最新已完成检查："])
    for check in report.latest_checks:
        lines.extend(_check_lines(check))
    if not report.latest_checks:
        lines.append("暂无已完成检查；不能据此判断网站是否健康。")
    message.set_content("\n".join(lines) + "\n")
    return message


def send_alert(report: Report, mail_settings: MailSettings) -> None:
    """发送一封报告；无事件不连接 SMTP，失败抛 MailError，不修改通知状态。

    调用方应先取得 pending_report，仅在结果非 None 时调用。此处不自行重试：
    失败由调用方记入 deliveries，等待配置的最小邮件间隔后再尝试，避免连续发信。
    """
    if not report.events:
        return
    try:
        message = _build_message(report, mail_settings)
        # create_default_context 开启系统受信任证书与主机名校验；不禁用 TLS 校验。
        # with 负责正常 QUIT 和异常时关闭连接；不用 set_debuglevel 输出认证交互。
        with smtplib.SMTP_SSL(
            mail_settings.host,
            mail_settings.port,
            timeout=mail_settings.timeout_seconds,
            context=ssl.create_default_context(),
        ) as smtp:
            # SecretStr 只在登录调用这一处解开，不放入日志、正文或异常消息。
            smtp.login(
                mail_settings.username, mail_settings.password.get_secret_value()
            )
            refused = smtp.send_message(
                message,
                from_addr=mail_settings.sender,
                to_addrs=mail_settings.recipients,
            )
            # send_message 在部分收件人被拒绝时返回字典而非抛异常。
            # 只要有人未获 SMTP 接收，就按失败保留整批；下次可能重复发送给已接收者。
            if refused:
                raise MailError("SMTP 未接受全部收件人，本批事件保留待重试")
    except (smtplib.SMTPException, OSError, ValueError):
        # 原始异常可能回显服务端信息甚至凭证；默认 traceback 也不展示异常链。
        # SMTP 接收不等于最终送达邮箱，不承诺投递到收件箱或严格仅发送一次。
        raise MailError("告警邮件发送失败，请检查 SMTP 配置或网络") from None
