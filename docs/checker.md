# 页面检查与重试

## 当前能力与阅读顺序

任务 2 对应 Issue #6、分支 `codex/6-page-checks-retry`。实现位于 `src/website_healthcheck/checker/checker.py`，`checker/__init__.py` 标识检查包；测试位于 `tests/test_checker.py`，通过 `from website_healthcheck.checker import checker` 导入。建议从 `run_checks` 读起，再看 `check_page` 的重试循环，最后看 `_check_once` 的单次请求。

`run_checks(config, client)` 按配置顺序返回每个目标的 CheckResult；`check_page(target, settings, client)` 完成一个页面的全部尝试。调用方使用 `with httpx.Client() as client` 创建并关闭客户端，两个函数复用该客户端；每次响应由检查函数关闭。主程序入口为 `src/website_healthcheck/main.py`，步骤 5 已接入整轮执行，具体顺序见 [命令与调度说明](scheduling.md)。

## 请求与判定

使用 GET 并跟随重定向，判断最终响应。三个条件分别从 CheckSettings 读取，默认要求状态码 200、完整响应耗时不超过 5 秒、响应文本区分大小写包含 Canlah。无需解析 DOM，也不在非 200 时提前停止其他判断。

开始时间使用 UTC；耗时使用 `time.monotonic()`，从发送请求前计到最终正文完整读完，包含所有重定向和正文传输。等于 5 秒通过，重试等待不计入单次耗时。HTTPX 的阶段超时默认 10 秒，与 5 秒判断阈值独立；它不能保证请求总时长不超过 10 秒，见 [HTTPX 超时说明](https://www.python-httpx.org/advanced/timeouts/)。

每次失败后等待配置的间隔再试，默认首次请求之外最多重试 2 次、每次间隔 2 秒。状态、耗时、关键词、超时和网络失败都适用重试。成功后立即结束该目标，最后一次失败后不等待，继续下一个目标。

## 结果接口

Attempt 和 CheckResult 使用标准库 dataclass，仅保存结构化摘要；它们不负责验证 YAML，也不保存 HTML 或底层异常文本。字段冻结、尝试集合使用 tuple，防止后续处理修改历史记录。

| 对象 | 字段 | 含义 |
| --- | --- | --- |
| CheckResult | target | 原始检查目标字符串 |
| CheckResult | attempts | 非空的 Attempt 元组，由 check_page 生成 |
| CheckResult | success | 派生属性：最后一次尝试无失败原因时为 True |
| Attempt | number / started_at | 从 1 起的序号、UTC 开始时间 |
| Attempt | elapsed_seconds | 当前尝试耗时，失败请求也记录 |
| Attempt | final_url / status_code | 最终响应 URL 和状态码；未取得时为 None |
| Attempt | keyword_found | 完整正文是否含关键词；正文未读完时为 None |
| Attempt | failure_codes | 失败原因元组；成功为空 |

正常收到完整响应时，原因可同时包含 `status`、`latency`、`keyword`。HTTPX 超时归类 `timeout`，其他 RequestError（如连接、协议、解码错误、重定向循环）归类 `network`，单独记录，不判断不完整页面。读取失败前已取得的 URL 和状态码保留。程序自身的意外异常继续抛出，避免伪装成网站故障。

例如先失败再恢复时，`attempts` 保留失败历史，`success` 为 True。后续存储模块据最终结果判断是否产生故障事件，检查模块本身不写库或发信。

## pytest 与验证范围

```bash
uv sync --locked
uv run --locked pytest tests/test_checker.py -v
uv run --locked pytest -q
```

2026-10-18，本机 Python 3.12：新增 36 项检查测试通过，连同原有 59 项配置与入口测试，共 95 项通过。测试使用 [HTTPX MockTransport](https://www.python-httpx.org/advanced/transports/#mock-transports) 替换传输，使用 monkeypatch 替换时钟和 sleep，因此不请求真实网站，也不会实际等待重试间隔。

覆盖成功、配置化判定、大小写、非 200、多项同时失败、5 秒边界、分块正文耗时与响应关闭、重定向累计耗时、各阶段超时分类、网络错误、正文中断、重定向循环、重试恢复与耗尽，以及单个目标失败后继续检查其他目标。

`uv build --out-dir /tmp/canlah-task2-dist` 构建 sdist 和 wheel 成功；归档检查确认包含 checker 子包、main.py 和新的命令映射，未包含真实 config.yaml 或数据库。

以上是任务 2 当时的离线验证；超时由替身主动抛出。步骤 6 另加本机 HTTP 集成测试，实际验证 socket、重定向、正文延迟和超时，并将 CI 改为完整 pytest，见 [统一测试说明](testing.md)。真实站点和 Gmail 尚未验证；远端 workflow 结果须在提交推送后核对。
