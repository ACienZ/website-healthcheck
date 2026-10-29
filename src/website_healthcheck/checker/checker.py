"""页面检查与重试：返回检查历史，不写数据库或发送邮件。

阅读顺序：run_checks → check_page → _check_once。
HTTPX Client 由调用方创建并关闭，使多个页面、多个尝试能够复用连接。
"""

from dataclasses import dataclass
from datetime import UTC, datetime
import time
from typing import Literal

import httpx

from website_healthcheck.config.load_config import CheckSettings, Config

# Literal 限定代码中使用的原因名称，便于后续存储和邮件模块统一处理。
# timeout 单独表示等待超时；network 包括连接、协议、解码和重定向等请求错误。
FailureCode = Literal["status", "latency", "keyword", "timeout", "network"]


@dataclass(frozen=True)
class Attempt:
    """一次 HTTP 尝试的摘要；不保存响应正文或底层异常消息。"""

    # dataclass 自动生成 __init__、repr 等方法；此处只是内部数据记录，
    # 无须重复执行配置模块那样的 Pydantic 输入校验。
    # frozen=True 防止创建后误改字段，保留当次尝试的原始记录。
    number: int
    started_at: datetime
    elapsed_seconds: float
    final_url: str | None
    status_code: int | None
    keyword_found: bool | None
    failure_codes: tuple[FailureCode, ...]


@dataclass(frozen=True)
class CheckResult:
    """一个目标的全部尝试；是否成功只取决于最后一次尝试。"""

    target: str
    attempts: tuple[Attempt, ...]

    @property
    def success(self) -> bool:
        # property 允许调用方通过 result.success 读取派生值，
        # 不额外保存一份可能与 attempts 不一致的布尔状态。
        return not self.attempts[-1].failure_codes


def _check_once(
    target: str, settings: CheckSettings, client: httpx.Client, number: int,
) -> Attempt:
    """执行一次请求：完整收到正文后，同时判断三个检查条件。"""
    started_at = datetime.now(UTC)
    # UTC 时间供历史记录展示，单调时钟供耗时计算，避免系统校时影响结果。
    started = time.monotonic()
    final_url = None
    status_code = None
    keyword_found = None
    failures: list[FailureCode] = []

    try:
        # stream 先取得响应头，再由 read 显式读完整个响应体。
        # 计时从发请求之前开始，包含所有重定向以及正文传输。
        # with 保证读取失败时也关闭响应；客户端仍由调用方管理。
        with client.stream(
            "GET", target, follow_redirects=True,
            timeout=settings.request_timeout_seconds,
        ) as response:
            final_url = str(response.url)
            status_code = response.status_code
            response.read()
            elapsed = time.monotonic() - started
            keyword_found = settings.keyword in response.text

        # 不调用 raise_for_status：我们需要同时记录状态、耗时和关键词失败，
        # 而不是遇到非 2xx 响应就提前抛异常并漏掉其他检查项。
        if status_code != settings.expected_status:
            failures.append("status")
        if elapsed > settings.max_response_seconds:
            failures.append("latency")
        if not keyword_found:
            failures.append("keyword")
    except httpx.RequestError as exc:
        elapsed = time.monotonic() - started
        # 正文未读完时不判断关键词，也不把不完整响应当作正常页面检查。
        # 若已收到响应头，仍保留状态码和最终 URL，便于定位读取失败。
        # 只保存分类代码，底层异常可能带 URL/代理等信息，不写入结果。
        failures.append("timeout" if isinstance(exc, httpx.TimeoutException) else "network")

    return Attempt(
        number=number, started_at=started_at, elapsed_seconds=elapsed,
        final_url=final_url, status_code=status_code, keyword_found=keyword_found,
        failure_codes=tuple(failures),
    )


def check_page(target: str, settings: CheckSettings, client: httpx.Client) -> CheckResult:
    """检查一个页面；首次失败后，最多额外重试 settings.retries 次。"""
    attempts = []
    # retries=2 表示总共最多 3 次请求；range 的右边界不包含在循环中。
    for number in range(1, settings.retries + 2):
        attempt = _check_once(target, settings, client, number)
        attempts.append(attempt)
        if not attempt.failure_codes:
            break
        # 仅在还会有下一次尝试时等待；成功或最后一次失败后都不 sleep。
        if number <= settings.retries:
            time.sleep(settings.retry_delay_seconds)
    return CheckResult(target=target, attempts=tuple(attempts))


def run_checks(config: Config, client: httpx.Client) -> list[CheckResult]:
    """按配置顺序检查所有目标；请求失败被记录，不阻断下一个目标。"""
    # 共享同一个 Client；Pydantic HttpUrl 在 HTTP 边界转为字符串。
    return [check_page(str(target), config.check, client) for target in config.targets]
