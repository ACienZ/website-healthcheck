# 测试方法与验证记录

## 如何运行与阅读 pytest

在仓库根目录执行：

```sh
uv sync --locked
uv run --locked pytest
uv run --locked pytest tests/test_http_integration.py -v
```

第一条按锁文件安装环境；第二条运行全部测试；第三条只运行新增的本机 HTTP 集成测试。`-v` 显示每条用例名称，便于初次阅读 pytest；`-q` 用于更简洁的结果。已有依赖已包含 pytest，无须另外安装测试库。

pytest 按文件名 `test_*.py` 和函数名 `test_*` 查找测试；每个 `assert` 是一个验收条件。`@pytest.mark.parametrize` 把多组输入展开为多个独立用例，所以函数数不等于测试数。

`@pytest.fixture` 准备可复用资源，测试函数用参数名领取。`tmp_path` 为每条测试提供独立临时目录；`monkeypatch` 临时替换依赖、环境变量或时钟，测试结束自动还原；fixture 中的 `yield` 前准备、之后清理。断言失败也要关闭数据库、HTTP 服务和子进程，避免影响后续测试。

## 各层测试验证什么

| 文件 | 方法与覆盖范围 |
| --- | --- |
| tests/test_load_config.py | 临时 YAML、示例配置；类型与边界校验、路径、默认值、凭证脱敏 |
| tests/test_checker.py | HTTPX MockTransport、可控时钟；完整响应判断、精确 5 秒边界、分类及重试等待 |
| tests/test_storage.py | 真实临时 SQLite；运行/尝试记录、漏跑、中断、通知、写入失败回滚 |
| tests/test_notifier.py | SMTP_SSL 替身；实际邮件编码、汇总、TLS 参数、认证和发送失败、部分拒收 |
| tests/test_main.py | 临时配置、真实 SQLite/flock、HTTP/SMTP 替身；整轮次序、状态、退出码与旧任务处理 |
| tests/test_process_lock.py | 真实子进程和文件锁；SIGTERM、SIGKILL、同一时段保护、进程身份校验及接替 |
| tests/test_cron.py | 真实 shell、生成后的命令、uv 启动器；路径引用、日志追加、跨目录及退出码，不安装 crontab |
| tests/test_release.py | 临时 Git 仓库验证版本与合并状态；读取实际 workflow，执行其中的发布 shell，使用假 gh 验证重复跳过及失败停止 |
| tests/test_http_integration.py | 本机 HTTP 服务器、真实 HTTPX 连接；重定向、分段正文、读取超时、连接失败、重试、命令和通知状态配合 |

单元测试将时间变为确定数字，用于严格验证阈值和重试等待；本机 HTTP 测试真正传输数据、等待延迟，不替换 HTTP 的计时。两者互补，不能拿模拟出来的超时作为真实网络验证证据。

本机 HTTP 服务用 Python 标准库 `ThreadingHTTPServer`，只监听 `127.0.0.1`，端口由操作系统分配。临时配置把所有目标改为该服务；正文和状态码由测试控制。没有读取用户的真实 `config.yaml`，没有在项目 `config/var/` 写入运行数据。标准库测试服务不用于生产部署，参考 [Python 文档](https://docs.python.org/3.12/library/http.server.html)。

正文延迟测试先发送 `Can`，稍后才发送 `lah`，要求完整读取后发现关键词且将等待计入耗时；读取超时测试实际等不到剩余正文；协议错误测试故意少发声明长度的正文。不可用端口可能被系统立即拒绝，也可能等到超时，因此接受 `network` 或 `timeout`，均要求没有伪造的状态码和关键词结果。精确 5 秒边界仍由受控时钟的单元测试保证，不让真实机器负载决定边界。

主流程集成保留真正的配置加载、HTTP、锁、SQLite、报告查询和 MIME 邮件生成，只替换 `SMTP_SSL` 的网络出口。验证重试恢复不告警、重试耗尽后继续检查其他目标、SMTP 成功才标记通知、SMTP 失败返回 2 并保留事件，以及下一轮恢复后补通知历史故障。

已安装命令另在真实子进程中从其他目录运行，用健康的本机页面验证入口、HTTP 和持久化；该子进程不继承 SMTP 替身，但健康且无历史事件，所以不会连接 SMTP。其余退出码、漏跑及旧实例终止的场景由此前主流程和真实进程锁测试覆盖。

## GitHub Actions

`.github/workflows/ci.yml` 从环境检查升级为业务测试，在 push 和 pull_request 上运行 Ubuntu job：

1. 检出当前提交，安装与本机一致的 uv 0.9.28。
2. `uv python install` 根据 `.python-version` 准备 Python 3.12。
3. `uv sync --locked` 安装锁定依赖。
4. `uv run --locked pytest` 运行完整测试，包括本机 HTTP 服务和真实子进程。

workflow 使用只读仓库权限和十分钟超时，不配置 Gmail Secrets，不执行健康检查命令或安装系统 cron。测试使用自己的临时配置和 SMTP 替身；依赖安装需要网络，但业务测试不依赖外网网站或真实 Gmail。uv 用法参考 [官方 Actions 集成文档](https://docs.astral.sh/uv/guides/integration/github/)。

本地 pytest 成功不能当作 Linux Actions 已通过；请核对本步骤 PR Checks 或对应提交的实际运行结果。步骤 7 的发布 workflow 单独在标签推送时重新运行完整测试，通过后才允许创建 Release，详见 [发布说明](releases.md)。

## 步骤 6 验证记录

环境为 2026-10-18 的 Mac、Python 3.12、uv 0.9.28。步骤 5 的基线为 240 项；步骤 6 新增 14 项本机 HTTP 集成用例。

- `uv run --locked pytest -q`：254 项通过，6.61 秒；含全部本机 HTTP 用例及原有模块、真实锁和子进程测试。
- `/bin/sh -n scripts/run_healthcheck.sh`：语法检查通过。
- README 共 9 行，本地 Markdown 文件链接和 CI YAML 结构检查通过；部署 SQL 已在临时 SQLite 表结构上执行通过。
- `uv build --out-dir /tmp/canlah-task6-dist`：sdist 和 wheel 构建通过；源码归档包含 README、新测试和部署文档，wheel 包含主入口与 scheduler；归档检查确认未包含真实配置、运行数据库、锁、日志或本地 `.vscode/`。
- 远端 Linux Actions：执行完整 pytest，实际通过情况以本步骤 PR Checks 为准。

以上行数与构建记录对应首次本地验收。之后在当时的文档审阅中提供各 10 行的英文 / 中文 README，并将详细说明拆成 PROJECT_GUIDE.md / PROJECT_GUIDE_CN.md；当时仅检查文档链接、章节和双语命令一致性，不重复运行 pytest。2026-10-29 的展示文档调整已将详细说明整合为当前[英文 README](../README.md) / [中文 README](../README_CN.md)，移除旧指南；历史行数不再描述当前首页。

用户最终审阅后，保留其 README 语言入口和布局，重新构建至 `/tmp/canlah-task6-final-dist` 并检查归档：源码包包含最终中英文 README、项目指南、测试及部署文档，wheel 包含主入口和 scheduler；真实配置、运行数据库、锁、日志与 `.vscode/` 仍排除。同步检查最终文档链接、章节跳转及配置/命令参数。

初次完整测试发现本机不可用端口返回等待超时，测试原先只接受立即拒绝连接；已按操作系统差异调整连接失败断言并缩短等待。没有为此修改业务代码，也没有改变 `timeout` 与 `network` 的分类规则。

真实服务反馈：用户于 2026-10-18 确认 Mac 本地真实测试已经完成且无问题。本记录基于用户反馈，未收集逐项命令、执行时间和日志；不推断覆盖了停机、休眠等全部异常场景，也不代表 Linux 部署主机已完成验证。本机 HTTP 自动测试与该手动测试是两类证据。

## 步骤 7 验证记录

以下为 2026-10-18 正式发版前的本地验证，Mac / Python 3.12.12 / uv 0.9.28：

- `uv run --locked pytest tests/test_release.py -q`：17 项通过，4.03 秒。
- `uv run --locked pytest`：271 项通过，10.74 秒；包含原有 254 项和新增发布测试。
- 临时 Git 仓库实际验证普通/带说明标签、main 后续前进、版本不符、未合并、错误检出、缺少 main 历史和 CLI 退出码；没有给本项目打标签。
- 从实际 release.yml 读取发布 bash 并执行，假 gh 验证不存在时创建、已有时跳过、标签精确匹配、查询失败停止和创建失败返回非零；workflow 结构检查确认测试任务先于有写权限的发布任务。
- `uv build --out-dir /tmp/canlah-task7-dist`：sdist 与 wheel 构建通过；归档检查确认源码含发布脚本、workflow、测试与文档，源码及 wheel 均排除真实配置、数据库、锁、日志和 .vscode/。
- 114 处本地 Markdown 文件/章节链接、双语命令一致性、两份 README 各 10 行、发布 bash 语法与完整暂存 diff 检查通过。
- 当时真实 `gh api --paginate` 只读查询成功，仓库尚无 Release；本地验证阶段没有执行真实创建命令。

随后已分别核对普通 CI 与真实标签触发的发布运行：

- [PR #19 的 Ubuntu/Python 3.12 CI](https://github.com/ACienZ/website-healthcheck/actions/runs/39096616003)：271 项通过（8.33 秒）。
- 用户合并 PR #19 后，本地在实际合并提交 `76ada0b` 执行 `uv sync --locked` 成功，完整 pytest 271 项通过（13.64 秒）。
- 用户授权推送 v0.1.0 后，[正式 Release Actions](https://github.com/ACienZ/website-healthcheck/actions/runs/39096892581) 的 Linux 完整 pytest 271 项通过（7.75 秒），随后发布任务成功创建 [v0.1.0](https://github.com/ACienZ/website-healthcheck/releases/tag/v0.1.0)。
- 同一提交的 [main Tests CI](https://github.com/ACienZ/website-healthcheck/actions/runs/39096753303) 和 [标签 Tests CI](https://github.com/ACienZ/website-healthcheck/actions/runs/39096892582) 均成功；实际发布说明和源码归档已检查，详见 [正式发布记录](releases.md#v010-正式发布记录)。

本地发布测试、普通 CI 与实际创建 Release 是三类验证；均未通过业务测试连接真实 Gmail 或安装系统 cron。用户的 Mac 真实运行反馈和 Linux 生产主机验收仍按前述边界区分。

## 完整改名的本地验收（2026-10-29）

本轮为 website-healthcheck / website_healthcheck 更名及 0.2.0 准备：

- `uv sync --locked --offline` 通过；锁文件比较确认全部依赖版本、来源、哈希与元数据不变，仅本项目名称和版本变化。
- `uv run --locked pytest -q`：272 项通过，11.33 秒。原 271 项覆盖同步迁移到新包和命令，新增已安装 `website-healthcheck --help` 从空目录运行的子进程测试，验证无需真实配置且不会生成运行文件。
- shell 语法检查通过；124 处本地文档文件/章节链接及 9 组双语 shell/cron 示例一致性通过。
- `uv build --offline --out-dir /tmp/website-healthcheck-0.2.0-dist` 成功，sdist 有 50 个文件，新包、命令映射、0.2.0 与 MIT 正确，未包含真实配置、数据库、日志、.vscode/ 或 .venv/。
- wheel 在临时独立虚拟环境安装后，从项目外执行 --help 与导入成功，读取分发元数据为 website-healthcheck 0.2.0。
- 本地配置与运行数据仅随包目录迁移，文件大小及修改时间不变；示例配置内容与改名前一致。没有请求真实网站、连接 Gmail 或编辑系统 cron。

以上为本地结果；改名提交的 Linux CI、实际标签和 Release 仍需在后续推送、审阅合并及发布后分别核对。

## 真实验证方法与已知边界

在其他部署机器复验时，按 [部署说明](deployment.md) 使用自己的配置先手动运行，再安装 cron，记录执行时间、命令、退出码、数据库变化和邮件是否收到。可临时把真实配置中的关键词换为一个确定不存在的值制造故障；验证后必须恢复 `Canlah`。不要为了测试删除历史库或放宽 TLS 校验。

| 场景 | 当前行为与后续验收重点 |
| --- | --- |
| 网站失败后重试恢复 | 保留尝试历史，不产生最终故障通知 |
| 一直失败 | 重试耗尽后保存最终故障，当前检查全部结束再汇总 |
| cron 根本没启动/机器停机 | 后续启动才识别过去缺口，不补跑；持续停机无法本机自我告警 |
| 旧时段实例卡住 | 下一时段核对身份、终止、确认拿到锁，再记中断并执行新轮，不继续旧轮 |
| SMTP 失败或部分拒收 | 记录失败并保留待通知事件，过最小邮件间隔再尝试；部分接受者可能重复收到 |
| SMTP 已接受、数据库还未标记时崩溃 | 下次可能重复通知；初版不能保证严格一次投递 |
| SMTP 接受但未进入收件箱 | 不能靠成功调用证明送达，需实际查看邮箱及投递限制 |

生产前的独立心跳、凭证管理、告警升级和数据维护建议见 [部署文档](deployment.md#生产前补充)。
