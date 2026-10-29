# Website Healthcheck TODO

更新日期：2026-10-29。初版步骤 0–7 已全部完成，v0.1.1 已发布并包含 MIT 许可证；展示文档已合并，当前进行 website-healthcheck 完整改名及 v0.2.0 准备。下方原始步骤与验收记录为历史资料，旧 README 行数不描述当前首页。

- Project：[Website Healthcheck Tracker](https://github.com/users/ACienZ/projects/12/views/1)。
- 初版发布：[步骤 7 #11](https://github.com/ACienZ/website-healthcheck/issues/11) 已关闭，[v0.1.0](https://github.com/ACienZ/website-healthcheck/releases/tag/v0.1.0) 已发布。
- 公开前收尾：[Issue #20](https://github.com/ACienZ/website-healthcheck/issues/20) 已关闭、PR #21 已合并，MIT 文件及包元数据已进入 main。
- 已完成版本任务：[Issue #22](https://github.com/ACienZ/website-healthcheck/issues/22) 已关闭，PR #23 已合并；[v0.1.1](https://github.com/ACienZ/website-healthcheck/releases/tag/v0.1.1) 于 2026-10-18 发布，2026-10-29 已回读核实。
- 展示文档：[Issue #24](https://github.com/ACienZ/website-healthcheck/issues/24) 已关闭，PR #25 已合并（7e20cbf），Project 为 Done，远端分支已删除。
- 当前任务：[Issue #26](https://github.com/ACienZ/website-healthcheck/issues/26)，关联分支 `codex/26-rename-website-healthcheck`；refactoring / documentation / testing，P1 / M / In progress。
- 设计：[实施设计与接口](docs/planning/implementation-plan.md)。
- 保留 [Issue #3：注意git流程](https://github.com/ACienZ/website-healthcheck/issues/3) 及其现有状态、标签。

## 初始化记录

- [x] Git 独立仓库、Python 3.12 uv 环境及锁文件。
- [x] 初始化 PR #2 已由用户 squash merge，远端初始化分支已删除。
- [x] AGENTS.md 已同步 main；初始化环境 CI 通过，不代表业务测试通过。
- [x] Project 访问已恢复，沿用用户在另一个会话中整理的字段与标签。

## 任务顺序

下表初版任务统一 P1。步骤 0–7 已 Done，均已由用户审阅合并；后续任务继续使用独立 Issue、分支和 PR。

| 步骤 | Issue | 状态 | Size |
| --- | --- | --- | --- |
| 0 | [固化需求、接口与实施计划](https://github.com/ACienZ/website-healthcheck/issues/4) | Done | S |
| 1 | [建立 src 包结构与配置管理](https://github.com/ACienZ/website-healthcheck/issues/5) | Done | M |
| 2 | [页面检查与重试](https://github.com/ACienZ/website-healthcheck/issues/6) | Done | M |
| 3 | [SQLite 执行与结果记录](https://github.com/ACienZ/website-healthcheck/issues/7) | Done | M |
| 4 | [Gmail SMTP 汇总告警](https://github.com/ACienZ/website-healthcheck/issues/8) | Done | M |
| 5 | [命令入口与 cron 调度](https://github.com/ACienZ/website-healthcheck/issues/9) | Done | M |
| 6 | [集成验证、CI 与说明文档](https://github.com/ACienZ/website-healthcheck/issues/10) | Done | M |
| 7 | [首次发布自动化](https://github.com/ACienZ/website-healthcheck/issues/11) | Done | M |

## 步骤 0 验收记录

- [x] 创建步骤 0–7 Issue 并加入 Project，设置状态、Priority 和 Size。
- [x] 记录需求、技术选型、模块接口、SQLite 状态、cron 与邮件语义。
- [x] 记录 pytest 单元/集成测试、CI 与首版 Release 验收。
- [x] 更新协作约定，将 config.yaml 加入 Git 忽略规则。
- [x] 用户已合并文档 PR #12，步骤 0 已标记 Done。

## 步骤 1 验收记录

- [x] 核对 PR #12 已合并，Project 中 Issue #5 为 In progress。
- [x] 核对 Issue #5 已关联 codex/5-src-config，本地分支跟踪对应远端，基线为 b958d8b。
- [x] 建立 src/canlah_healthcheck 包目录与 __init__.py。
- [x] 配置 Hatchling 构建及 src 包发现；uv sync 安装与包导入验证通过。
- [x] 添加 canlah-healthcheck 配置校验入口：成功 0、配置错误 2，明确不执行健康检查。
- [x] 使用 uv 添加 PyYAML、Pydantic 运行依赖及 pytest 开发依赖；锁文件同步与导入验证通过。
- [x] 实现 config/load_config.py 配置模型与 load_config(path)，校验字段、解析路径并对错误脱敏。
- [x] 配置与示例移入包内 config/，创建对应 var/ 占位目录；测试改名 test_load_config.py，关键逻辑添加中文注释。
- [x] 提供无真实凭证的 config.yaml.example；真实 config.yaml 被忽略，示例可跟踪。
- [x] 配置 pytest：55 项通过，覆盖配置边界、路径解析及凭证脱敏；uv 包安装和导入已验证。
- [x] 验证已安装命令从其他目录运行；合计 59 项 pytest 通过，sdist/wheel 构建通过且未包含真实配置。
- [x] 检查完整 diff，补充配置使用说明与验证记录。
- [x] 用户已审阅并合并 PR #13，Project 中步骤 1 为 Done；合并提交为 71a9e54。

## 步骤 2 验收记录

- [x] 核对 PR #13 已合并，Project 中 Issue #6 为 In progress。
- [x] 从最新 main 创建并关联 codex/6-page-checks-retry，本地跟踪对应远端，基线为 71a9e54。
- [x] 使用 uv 添加已确认的 HTTPX 同步客户端依赖，并同步环境与锁文件。
- [x] 定义 Attempt、CheckResult，记录每次尝试及最终结果。
- [x] 实现 check_page：完整响应计时、状态码、关键词、网络失败与重试。
- [x] 实现 run_checks：复用客户端，顺序检查，单个失败不阻断其他目标。
- [x] 按用户要求将检查模块归入 checker/ 包，主程序改名 main.py；同步命令映射、导入、test_main.py 与文档。
- [x] 添加带中文注释的 pytest：36 项离线检查测试通过，连同原有测试合计 95 项通过。
- [x] 更新设计与检查模块说明，检查完整 diff 并记录验证结果。
- [x] uv 构建 sdist/wheel 通过，检查归档内的 checker 子包、main 入口及真实配置排除规则。
- [x] 用户已确认当前实现并授权提交、推送和创建 PR。
- [x] 用户已审阅并合并 PR #14，Project 中步骤 2 为 Done；合并提交为 31f5ca9。

## 步骤 3 验收记录

- [x] 核对 PR #14 已合并，Project 中 Issue #7 为 In progress。
- [x] 从最新 main 创建并关联 codex/7-sqlite-execution-records，本地跟踪对应远端，基线为 31f5ca9。
- [x] 第一段：建立 storage/ 包、SQLite 表及连接管理；记录运行、页面结果、各次尝试与最终故障事件。
- [x] 验证唯一计划时段、重试恢复不告警、正常完成、数据库重开与事务回滚；新增 21 项测试，合计 116 项通过。
- [x] 根据用户审阅补充四张表及字段用途、关联关系、实际重试次数、Storage 类职责和编号写入流程注释；业务逻辑不变。
- [x] 第二段：首次调度基线、宽限边界、漏跑区间、异常中断与周期变更；不补跑历史请求。
- [x] 第三段：通知状态记录与查询，覆盖失败保留、成功标记和幂等边界。
- [x] 任务 3 共新增 47 项临时 SQLite 测试；全套 142 项通过，覆盖恢复/通知事务回滚和重开数据库后的幂等行为。
- [x] 更新设计与模块说明，检查完整 diff，完成任务 3 本地验收；sdist/wheel 构建及归档内容检查通过。
- [x] 用户授权完成任务后提交、推送并创建 PR；提交包含 Codex 共同作者署名。
- [x] 用户已审阅并合并 PR #15，Issue #7 已关闭、远端分支已删除，Project 为 Done；合并提交为 d98fb26。

## 步骤 4 验收记录

- [x] 核对 PR #15 已合并、Issue #7 已关闭；将 Project 中 Issue #8 从 TODO 调整为 In progress。
- [x] 从最新 main 创建并关联 codex/8-gmail-smtp-alerts，本地跟踪对应远端，基线为 d98fb26。
- [x] 创建 notifier/ 包，使用标准库生成纯文本汇总邮件，保留历史故障并附各目标最新状态、漏跑和中断信息。
- [x] 实现 send_alert：SMTP_SSL、证书与主机名校验、配置参数、全部收件人检查及错误脱敏；不写数据库、不在内部重试。
- [x] 新增 19 项带中文说明的离线邮件测试，验证 SMTP 替身与临时 SQLite 配合；全套 161 项 pytest 通过。
- [x] sdist/wheel 构建通过，归档包含 notifier 子包，且未包含真实配置、运行数据库或锁文件。
- [x] 更新设计与邮件模块说明，检查本任务 diff；未连接真实 Gmail，不将离线验证当作真实投递成功。
- [x] 用户已审阅实现，并授权提交、推送和创建 PR；提交包含 Codex 共同作者署名。
- [x] 用户已合并 PR #16，Issue #8 已关闭、远端分支已删除，Project 为 Done；合并提交为 c6d70e3。

## 步骤 5 验收记录

- [x] 核对 PR #16 已合并、Issue #8 已关闭；将 Project 中 Issue #9 调整为 In progress。
- [x] 从最新 main 创建并关联 codex/9-cli-cron，本地跟踪对应远端，基线为 c6d70e3。
- [x] 主入口串联配置、旧实例终止、过去任务核对、检查、保存、汇总邮件及投递状态，落实 0/1/2/3 退出码。
- [x] 新时段实例先核对并终止旧实例，拿到 flock 后再打开数据库；锁覆盖 HTTP 和 SMTP，同一时段不重复启动。
- [x] 提供绝对路径 shell 启动器和只输出 cron 片段的辅助脚本，不安装或覆盖 crontab。
- [x] 调度相关 74 项测试、全套 240 项 pytest 通过；shell 语法、sdist/wheel 构建与归档内容检查通过。
- [x] 构建排除本地 .vscode/，保留其原文件；归档不包含真实配置、数据库、锁或日志。
- [x] 同步白话注释、设计、使用说明与 Issue #9 中的新规则，已检查完整 diff；用户已审阅实现。
- [x] 按用户本轮审阅修改：新时段启动时终止仍未结束的旧实例，确认停止后记中断并执行新一轮，不补跑旧任务；邮件阶段被终止也记中断并保留检查结果。
- [x] 用户已批准并通过 uv 新增 psutil；完成 PID/创建时间/锁文件身份检查、终止等待上限及真实子进程接替测试。
- [x] 已把 reconcile_schedule 的职责和五段逻辑改为白话说明，说明“补上数据库状态”不表示重新执行旧任务。
- [x] 只加载用户现有真实配置，字段校验通过；没有请求网站、连接 SMTP 或输出配置内容。
- [x] 用户已审阅实现，授权提交、推送并创建 PR；提交包含 Codex 共同作者署名。
- [x] 用户已合并 PR #17，Issue #9 已关闭，Project 为 Done；合并提交为 41b46ea。

## 步骤 6 验收记录

- [x] 核对 PR #17 已合并，Project 中 Issue #10 已调整为 In progress。
- [x] 从最新 main 创建并关联 codex/10-integration-ci-docs，本地跟踪对应远端，基线为 41b46ea。
- [x] 新增 14 项本机 HTTP 集成用例，覆盖真实连接、重定向、完整正文耗时、读取超时、连接/协议失败与重试。
- [x] 将本机 HTTP、SQLite、邮件替身与命令入口串联，验证保存、通知状态、恢复后历史汇总和跨目录已安装命令。
- [x] GitHub Actions 从环境检查改为 Python 3.12 完整 pytest；实际远端结果以本步骤 PR Checks 为准。
- [x] 提供符合 5–10 行要求的英文 README、中文部署维护及 pytest 说明，同步历史模块文档中的 CI 状态。
- [x] 按用户审阅整理 PROJECT_GUIDE.md 英文版和 PROJECT_GUIDE_CN.md 中文版，完整介绍功能、模块、运行逻辑、配置、部署及维护，支持语言切换。
- [x] 审阅用户调整的 README，保留分组结构、修正配置不会自动更新 cron 的表述，控制为 10 行并链接双语指南；逐项解释生产准备建议，未实施这些扩展功能。
- [x] 再次审阅 README，修正“本轮成功等于没有待通知事件”的歧义；新增 README_CN.md 并加入双向链接，两份均为 10 行。
- [x] 全套 254 项 pytest、shell 语法、README 行数、文档链接、CI YAML 及部署 SQL 检查通过；sdist/wheel 构建与内容排除检查通过。
- [x] 已暂存本任务文件并检查完整 diff（含新增文件），记录验证证据；未纳入用户的 .vscode/，保留用户最新 README 布局。
- [x] 用户已审阅实现及文档，授权提交、推送并创建 PR；提交包含 Codex 共同作者署名。
- [x] 用户已合并 PR #18、关闭 Issue #10 并删除远端分支；Project 为 Done，合并提交 dfed3db。

## 步骤 7 验收记录

- [x] 核对 PR #18 已合并、Issue #10 已关闭；将 Project 中 Issue #11 设为 In progress。
- [x] 从最新 main（dfed3db）创建并关联 codex/11-release-automation，本地已切换并跟踪远端同名分支。
- [x] 实现 v* 标签发布流程：版本一致、标签提交已进入 main、完整 pytest 通过，才能创建 GitHub Release。
- [x] 验证已有 Release 不覆盖，查询/测试失败时不发布；不上传 PyPI 或额外构建资产。
- [x] 补充发布说明，同步双语指南的 cron 删除步骤，删除末尾过时备注。
- [x] 用户反馈 Mac 本地真实测试已完成且无问题；记录为用户确认，不编造执行日志或逐项测试数据。
- [x] 发布专项 17 项、完整 pytest 271 项通过；sdist/wheel 构建通过，源码包含发布脚本与文档。
- [x] 已检查完整暂存 diff（含新增文件）、114 处本地文档链接、双语命令、README 行数、发布 shell 语法及归档内容；未纳入 .vscode/。
- [x] 用户已审阅实现及发布步骤，授权 commit、推送与创建 PR；提交包含 Codex 共同作者署名。
- [x] 用户已合并 PR #19（76ada0b）、关闭 Issue #11 并删除远端分支；Project 为 Done。
- [x] 获得用户发版授权后，同步 main，在实际合并提交上运行完整 pytest：271 项通过（13.64 秒），再推送带说明标签 v0.1.0。
- [x] [正式 Release Actions](https://github.com/ACienZ/website-healthcheck/actions/runs/39096892581) 校验成功，Linux 完整 pytest 271 项通过（7.75 秒），随后成功创建 [v0.1.0](https://github.com/ACienZ/website-healthcheck/releases/tag/v0.1.0)。
- [x] 核对自动发布说明及 GitHub 源码归档：必要文件齐全，不含真实配置、数据库、日志或本地环境文件；发布详情见 [发布记录](docs/releases.md#v010-正式发布记录)。

## 公开前收尾记录

- [x] 关联 [Issue #20](https://github.com/ACienZ/website-healthcheck/issues/20)，同步 TODO、设计、测试与正式发布记录。
- [x] 按用户选择添加 MIT LICENSE（2026 ACienZ）、包许可证元数据和双语 README 入口。
- [x] 完整 pytest 271 项通过（10.83 秒）；121 处文档链接/章节及 README 各 10 行检查通过；uv sync --locked 与 sdist/wheel 构建通过，归档含 MIT 文件，wheel 声明 MIT 元数据，真实配置与运行数据仍排除。
- 本收尾使用独立 PR 交付，由用户合并后自行公开仓库与 Project；不移动 v0.1.0 标签或改写已发布归档。

## v0.1.1 准备记录

- [x] PR #21 已合并（cbd135b），Issue #20 已关闭、Project 为 Done；从最新 main 创建 Issue #22 对应分支。
- [x] 将 main 的 protect-main Ruleset 合并方式限制为 Squash；回读核对仅此项变化，PR、pytest、最新 main、讨论解决、禁止删除和强推等规则保持不变。
- [x] 项目版本更新为 0.1.1，同步 uv.lock，不升级依赖；更新双语指南与发布说明。
- [x] uv sync --locked 成功，完整 pytest 271 项通过（11.12 秒）；123 处文档链接/章节和两份 README 各 10 行检查通过；sdist/wheel 的版本、MIT 内容及运行数据排除检查通过。
- v0.1.0 的源码快照没有 LICENSE。版本 PR 合并后，按 [发布步骤](docs/releases.md#合并后的发布操作) 创建 v0.1.1，使新归档携带 MIT；不移动旧标签，不提前将新版本记为已发布。

## 实施边界

步骤 0–7 已合并并完成初版发布，历史验收记录见 [配置说明](docs/configuration.md)、[检查说明](docs/checker.md)、[存储说明](docs/storage.md)、[邮件说明](docs/notifier.md) 和 [命令与调度说明](docs/scheduling.md)。

步骤 6 补充本机 HTTP 集成测试、业务 pytest CI 和使用说明。用户已确认 Mac 本地真实测试通过；自动测试不安装系统定时任务、不读取真实配置或发送真实邮件。步骤 7 的发布自动化已在 v0.1.0 标签上实际运行成功；Linux 生产主机部署仍需单独验收。

环境复现：`uv sync --locked`。统一测试命令：`uv run --locked pytest`。合并提交的本地完整验证为 271 项通过，正式标签的 Linux Release CI 也为 271 项通过；其中 17 项发布单元测试使用临时 Git 仓库和假 gh，与真实发布运行分开记录。当前文档入口见[中文 README](README_CN.md) / [English README](README.md)，详见 [部署维护](docs/deployment.md)、[测试证据](docs/testing.md) 和 [发布记录](docs/releases.md)。以上 271 项为 2026-10-18 的历史验证结果；README 各 10 行是当时的交付要求，当前首页已整合为完整介绍。


## GitHub 展示文档整理（Issue #24）

- [x] 2026-10-29 核对远端 main 与本地一致，基线为 `9f18aeb5177a5c6a19c19686d8f6a02e3464028d`；Issue #22 已关闭、Project 为 Done，v0.1.1 已发布。
- [x] 创建 [Issue #24](https://github.com/ACienZ/website-healthcheck/issues/24)，加入 Project #12，设为 documentation / P1 / S / In progress；从 main 创建并检出关联分支 `codex/24-showcase-readme`。
- [x] 将完整中英文指南整合为 README，移除旧指南；更新展示定位、测试体验入口、默认目标来源、历史验证日期及已发布版本说明。
- [x] 更新协作约定、实施设计、部署/测试链接及历史题目说明，保留原始题目正文和历史验收记录。
- [x] 检查 14 份 Markdown 中 120 处本地文件/章节链接，全部通过；9 组双语 shell/cron 示例一致（退出码提示文字翻译除外）；对照原指南审阅完整迁移差异，git diff --check 通过。
- [x] 核实原始题目正文完整保留，旧指南文件已移除，现行 README 不含申请叙述；所有已跟踪改动均为 Markdown，Project 字段与关联分支已回读确认。
- [x] 用户已审阅本地文档，并于 2026-10-29 授权提交、推送及创建 PR。
- [x] 用户已于 2026-10-29 合并 [PR #25](https://github.com/ACienZ/website-healthcheck/pull/25)（7e20cbf），Issue 已关闭、Project 为 Done，远端分支已删除；本地已同步 main。分支归档留待本次改名合并后统一整理。

本轮仅修改文档，不运行 pytest 或真实网站、SMTP、cron；未改代码、配置、版本与锁文件。未跟踪的 .vscode/ 保留，不纳入任务；未修改上级资料。


## 完整改名与发布准备（Issue #26）

- [x] 用户确认完整改名为 website-healthcheck，仓库/命令与 website_healthcheck 包统一；以已合并的 PR #25 为基线创建独立 Issue 和关联分支。
- [x] GitHub 仓库更名为 ACienZ/website-healthcheck，Project #12 更名为 Website Healthcheck Tracker；origin 已同步新地址，可见性仍为 Private。
- [x] 迁移源码包目录，同步导入、命令、启动器、通知标题、打包配置、忽略规则和现行文档；本地配置及运行数据仅移动目录，文件大小与修改时间保持不变。原始示例网站与关键词不变。
- [x] 准备 0.2.0 项目版本；旧名称、旧版本、原始题目和历史验证证据保留为历史资料。
- [x] `uv lock --offline` 与 `uv sync --locked --offline` 通过；解析比较锁文件确认仅本项目名称、版本及排序变化，全部依赖元数据保持不变。
- [x] `uv run --locked pytest -q`：272 项通过（11.33 秒）；新增真实新命令 --help 的空目录子进程检查；shell 语法检查通过。
- [x] sdist/wheel 构建通过，源码包 50 个文件；新包目录、0.2.0、命令映射与 MIT 校验通过，未包含真实配置、运行数据、.vscode/ 或 .venv/。临时独立环境安装 wheel 后，新包导入与 --help 通过。
- [x] 124 处本地文档文件/章节链接及 9 组双语 shell/cron 示例一致性通过；原始示例配置与本地真实配置/运行数据属性保持不变；完整 diff 与空白检查通过。
- [x] 用户已审阅本地改名结果并授权提交、推送和创建 PR；提交包含 Codex 共同作者署名。
- [ ] 用户审阅改名 PR 并合并；随后整理本地分支和目录，再发布并核对 v0.2.0 的 Actions / Release / 源码归档。

本任务不修改现有业务规则，不安装 cron、不请求真实网站或发送邮件；.vscode/ 与本地配置/运行数据保留且不提交。项目归档由用户最终执行。
