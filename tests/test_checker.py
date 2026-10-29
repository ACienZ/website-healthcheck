"""页面检查的离线测试：替换 HTTP 传输和时钟，不请求外网或真实等待。

运行：uv run --locked pytest tests/test_checker.py -v
MockTransport 会把 HTTPX 请求交给本文件的 handler，仍经过 HTTPX 的
响应读取和重定向逻辑。它不建立真实连接，因此超时由测试主动抛异常模拟。
"""

from contextlib import ExitStack
from datetime import UTC
from pathlib import Path

import httpx
import pytest

from website_healthcheck.checker import checker
from website_healthcheck.config.load_config import CheckSettings, load_config

TARGET = "https://example.com/"
EXAMPLE = Path(__file__).resolve().parents[1] / "src/website_healthcheck/config/config.yaml.example"


class Clock:
    """可控的单调时钟：把耗时场景变成确定的数字，无须真的等几秒。"""

    def __init__(self):
        self.now = 0.0
        self.waits = []

    def monotonic(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds

    def sleep(self, seconds):
        # 同时记录等待并推进时间，用来检查重试延迟没有计入下次请求耗时。
        self.waits.append(seconds)
        self.advance(seconds)


@pytest.fixture
def clock(monkeypatch):
    """monkeypatch 临时替换模块使用的时钟；每项测试结束后自动恢复。"""
    value = Clock()
    monkeypatch.setattr(checker.time, "monotonic", value.monotonic)
    monkeypatch.setattr(checker.time, "sleep", value.sleep)
    return value


@pytest.fixture
def client_for():
    """返回客户端工厂，统一用 MockTransport，结束时关闭所有客户端。"""
    # ExitStack 管理数量不固定的 with 上下文。yield 前准备、yield 后清理，
    # 即使测试中的 assert 失败，pytest 也会继续执行清理代码。
    with ExitStack() as stack:
        def create(handler, **kwargs):
            return stack.enter_context(httpx.Client(
                transport=httpx.MockTransport(handler), **kwargs,
            ))
        yield create


@pytest.fixture
def settings():
    """每次新建配置；多数测试关闭重试，以便单独观察一次请求。"""
    return CheckSettings(retries=0)


def test_success_and_request_settings(clock, client_for, settings):
    """验证成功摘要、UTC 时间，以及实际传给 HTTPX 的方法和超时配置。"""
    settings.request_timeout_seconds = 7.0

    def handler(request):
        assert request.method == "GET"
        # HTTPX 将统一 timeout 展开为四个阶段；不是完整响应的总耗时上限。
        assert request.extensions["timeout"] == {
            "connect": 7.0, "read": 7.0, "write": 7.0, "pool": 7.0,
        }
        clock.advance(0.25)
        return httpx.Response(200, text="<html>Canlah</html>")

    result = checker.check_page(TARGET, settings, client_for(handler))
    assert result.target == TARGET
    assert result.success
    assert len(result.attempts) == 1
    attempt = result.attempts[0]
    assert attempt.number == 1
    assert attempt.started_at.tzinfo is UTC
    assert attempt.elapsed_seconds == 0.25
    assert attempt.final_url == TARGET
    assert attempt.status_code == 200
    assert attempt.keyword_found is True
    assert attempt.failure_codes == ()
    assert clock.waits == []


# parametrize 每一行都是独立用例；失败报告会指出是哪一组输入。
@pytest.mark.parametrize("status,body,failures", [
    (503, "Canlah", ("status",)),
    (204, "", ("status", "keyword")),
    (200, "canlah", ("keyword",)),
    (200, "CANLAH", ("keyword",)),
    (200, "", ("keyword",)),
])
def test_status_and_case_sensitive_keyword(clock, client_for, settings, status, body, failures):
    client = client_for(lambda request: httpx.Response(status, text=body))
    result = checker.check_page(TARGET, settings, client)
    assert not result.success
    assert result.attempts[0].failure_codes == failures


@pytest.mark.parametrize("elapsed,success", [(4.999, True), (5.0, True), (5.001, False)])
def test_latency_boundary(clock, client_for, settings, elapsed, success):
    """等于 5 秒通过，只有超过阈值才失败，避免写成 >=。"""
    def handler(request):
        clock.advance(elapsed)
        return httpx.Response(200, text="Canlah")

    result = checker.check_page(TARGET, settings, client_for(handler))
    assert result.success is success
    assert result.attempts[0].elapsed_seconds == elapsed
    assert result.attempts[0].failure_codes == (() if success else ("latency",))


def test_all_response_failures_recorded(clock, client_for, settings):
    """同一响应的三项判断都保留，不能遇到第一项失败就返回。"""
    def handler(request):
        clock.advance(6)
        return httpx.Response(500, text="unavailable")

    attempt = checker.check_page(TARGET, settings, client_for(handler)).attempts[0]
    assert attempt.failure_codes == ("status", "latency", "keyword")


def test_custom_response_rules(clock, client_for, settings):
    """阈值从配置读取，不能在实现中写死 200、Canlah 和 5 秒。"""
    settings.expected_status = 201
    settings.keyword = "custom"
    settings.max_response_seconds = 8.0

    def handler(request):
        clock.advance(7)
        return httpx.Response(201, text="custom")

    assert checker.check_page(TARGET, settings, client_for(handler)).success


class BodyStream(httpx.SyncByteStream):
    """模拟收到响应头之后的分块正文，可在读取中途抛异常。"""

    def __init__(self, clock, error=None):
        self.clock = clock
        self.error = error
        self.closed = False

    def __iter__(self):
        # 直到 response.read() 真正迭代正文时才推进时间，
        # 可抓住“只计响应头耗时，遗漏正文读取”的实现错误。
        self.clock.advance(3)
        yield b"Can"
        self.clock.advance(3)
        if self.error:
            raise self.error("test-only read failure")
        yield b"lah"

    def close(self):
        self.closed = True


def test_full_body_is_timed_and_closed(clock, client_for, settings):
    stream = BodyStream(clock)
    client = client_for(lambda request: httpx.Response(200, stream=stream))
    attempt = checker.check_page(TARGET, settings, client).attempts[0]
    assert attempt.elapsed_seconds == 6
    assert attempt.keyword_found is True
    assert attempt.failure_codes == ("latency",)
    assert stream.closed
    # 检查函数关闭每个响应，却不关闭由调用方传入的共享客户端。
    assert not client.is_closed


def test_redirect_time_and_final_response(clock, client_for, settings):
    visited = []

    def handler(request):
        visited.append(str(request.url))
        clock.advance(3)
        if request.url.path == "/":
            return httpx.Response(302, headers={"location": "/final"})
        return httpx.Response(200, text="Canlah")

    attempt = checker.check_page(TARGET, settings, client_for(handler)).attempts[0]
    assert visited == [TARGET, TARGET + "final"]
    assert attempt.final_url == TARGET + "final"
    assert attempt.status_code == 200
    assert attempt.keyword_found is True
    assert attempt.elapsed_seconds == 6
    assert attempt.failure_codes == ("latency",)


@pytest.mark.parametrize("error,code", [
    (httpx.ConnectTimeout, "timeout"),
    (httpx.ReadTimeout, "timeout"),
    (httpx.WriteTimeout, "timeout"),
    (httpx.PoolTimeout, "timeout"),
    (httpx.ConnectError, "network"),
    (httpx.ReadError, "network"),
    (httpx.RemoteProtocolError, "network"),
    (httpx.DecodingError, "network"),
    (httpx.ProxyError, "network"),
])
def test_request_errors_are_safe_results(clock, client_for, settings, error, code):
    """主动抛出 HTTPX 异常，验证分类；不声称测试了真实网络超时。"""
    secret = "test-only-sensitive-error"

    def handler(request):
        clock.advance(1)
        raise error(secret, request=request)

    result = checker.check_page(TARGET, settings, client_for(handler))
    attempt = result.attempts[0]
    assert not result.success
    assert attempt.failure_codes == (code,)
    assert attempt.status_code is None
    assert attempt.keyword_found is None
    assert attempt.final_url is None
    assert attempt.elapsed_seconds == 1
    assert secret not in repr(result)


@pytest.mark.parametrize("error,code", [(httpx.ReadTimeout, "timeout"), (httpx.ReadError, "network")])
def test_incomplete_body_is_not_a_keyword_result(clock, client_for, settings, error, code):
    stream = BodyStream(clock, error)
    client = client_for(lambda request: httpx.Response(200, stream=stream))
    attempt = checker.check_page(TARGET, settings, client).attempts[0]
    assert attempt.status_code == 200
    assert attempt.final_url == TARGET
    assert attempt.keyword_found is None
    assert attempt.failure_codes == (code,)
    assert attempt.elapsed_seconds == 6
    assert stream.closed


def test_redirect_loop_is_a_request_failure(clock, client_for, settings):
    client = client_for(
        lambda request: httpx.Response(302, headers={"location": "/"}),
        max_redirects=2,
    )
    result = checker.check_page(TARGET, settings, client)
    assert not result.success
    assert result.attempts[0].failure_codes == ("network",)


@pytest.mark.parametrize("failure", ["status", "latency", "keyword", "timeout", "network"])
def test_each_failure_can_recover_on_retry(clock, client_for, failure):
    """所有失败类型都重试；恢复后保留旧失败，但最终结果为成功。"""
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            if failure == "timeout":
                raise httpx.ReadTimeout("simulated", request=request)
            if failure == "network":
                raise httpx.ConnectError("simulated", request=request)
            clock.advance(6 if failure == "latency" else 0.25)
            return httpx.Response(
                503 if failure == "status" else 200,
                text="missing" if failure == "keyword" else "Canlah",
            )
        clock.advance(0.25)
        return httpx.Response(200, text="Canlah")

    result = checker.check_page(TARGET, CheckSettings(), client_for(handler))
    assert result.success
    assert len(calls) == 2
    assert [attempt.number for attempt in result.attempts] == [1, 2]
    assert result.attempts[0].failure_codes == (failure,)
    assert result.attempts[1].failure_codes == ()
    assert result.attempts[1].elapsed_seconds == 0.25
    assert clock.waits == [2.0]


@pytest.mark.parametrize("retries,delay", [(0, 2.0), (2, 2.0), (1, 0.0), (3, 0.5)])
def test_retries_exhausted_without_final_sleep(clock, client_for, retries, delay):
    """总请求数为 1+retries，等待次数为 retries；最后失败直接返回。"""
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(503, text="Canlah")

    settings = CheckSettings(retries=retries, retry_delay_seconds=delay)
    result = checker.check_page(TARGET, settings, client_for(handler))
    assert not result.success
    assert len(calls) == len(result.attempts) == retries + 1
    assert [attempt.number for attempt in result.attempts] == list(range(1, retries + 2))
    assert clock.waits == [delay] * retries


def test_run_checks_continues_after_failed_target(clock, client_for):
    """首个页面重试耗尽后，第二个仍执行；结果按配置顺序返回。"""
    config = load_config(EXAMPLE)
    targets = [str(target) for target in config.targets]
    visited = []

    def handler(request):
        visited.append(str(request.url))
        if str(request.url) == targets[0]:
            raise httpx.ConnectError("simulated", request=request)
        return httpx.Response(200, text="Canlah")

    results = checker.run_checks(config, client_for(handler))
    assert [result.target for result in results] == targets
    assert [result.success for result in results] == [False, True]
    assert visited == [targets[0]] * 3 + [targets[1]]
    assert clock.waits == [2.0, 2.0]


def test_unexpected_programming_errors_are_not_hidden(clock, client_for, settings):
    """仅处理 HTTP 请求错误；不能把程序自身的错误默默写成网站故障。"""
    def handler(request):
        raise ValueError("test programming error")

    with pytest.raises(ValueError, match="programming error"):
        checker.check_page(TARGET, settings, client_for(handler))
