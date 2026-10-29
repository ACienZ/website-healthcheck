# Website Healthcheck 实施设计

设计日期：2026-10-17；更新日期：2026-10-29。状态：步骤 0–7 已全部合并，步骤 7 为 PR #19（76ada0b），Issue #11 已关闭、Project 为 Done；[v0.1.0](https://github.com/ACienZ/website-healthcheck/releases/tag/v0.1.0) 已通过完整测试门禁并正式发布。模块说明见 [配置模块](../configuration.md)、[页面检查](../checker.md)、[SQLite 存储](../storage.md)、[汇总邮件](../notifier.md) 和 [命令调度](../scheduling.md)；统一使用与验收见 [部署说明](../deployment.md)、[测试说明](../testing.md) 和 [发布记录](../releases.md#v010-正式发布记录)。

## 1. 目标、范围与技术选型

历史原始要求见 [requirement.md](../../refs/requirement.md)（当前展示文档不再受其 README 行数限制）：检查首页和 playbook 页的状态码、耗时及 Canlah 关键词；任一检查最终失败返回非零；提供 5–10 行 README。用户追加配置管理、重试、SQLite、cron、Gmail 汇总告警、漏跑记录、pytest 及 GitHub Release。

初版单机运行，不使用消息队列、数据库服务器、网页界面或外部心跳服务。Mac 开发，Linux 部署。漏跑只记录并告警，不补跑历史任务；持续停机期间无法发信，恢复后识别。

| 职责 | 技术与理由 | 本轮状态 |
| --- | --- | --- |
| 环境 | Python 3.12、uv，沿用初始化配置 | 已有环境 |
| 包布局 | src/website_healthcheck；Hatchling 构建后端，显式选择 src 包；console script 为 website-healthcheck，入口映射到 main.py 中的 main | 步骤 1 已合并；步骤 2 按用户要求调整入口文件名 |
| 配置 | PyYAML safe_load + Pydantic v2；参考 GmailAutomate 的配置对象方式 | 步骤 1 已合并 |
| HTTP | HTTPX 同步 Client；两个页面顺序执行，复用连接，便于 MockTransport 测试 | 步骤 2 已合并 |
| 存储 | 标准库 sqlite3，事务管理，不引入 ORM | 步骤 3 已合并；步骤 5 已接入实际进程锁 |
| 邮件 | 标准库 smtplib、email；Gmail SMTP_SSL，465 端口，启用证书校验 | 步骤 4 已合并；真实投递另行验证 |
| 调度 | cron + shell 启动脚本；fcntl.flock 文件锁 + psutil 核对并终止旧实例（Linux/Mac） | 步骤 5 已合并 |
| 测试 | pytest，uv dev 依赖；fixture、tmp_path、monkeypatch；本机 HTTP 服务 | 步骤 6 补充集成测试和完整 pytest CI |
| 发布 | GitHub Actions 标签触发；GitHub Release 源码归档，不发布 PyPI | 步骤 7 已合并，v0.1.0 已发布 |

HTTPX 的网络超时是连接/读/写/连接池阶段的等待上限，不是整个响应的总时限。完整响应耗时用 monotonic/perf_counter 测量，包含重定向与响应体读取，不包含重试等待。不能把 HTTPX timeout 参数等同于 5 秒验收阈值；持续缓慢传输可能延长一轮执行，完整流程不承诺严格总运行时限；下一时段启动时按本轮确认的策略终止未结束的旧实例，再执行当前轮。

参考：[原配置目录](https://github.com/ACienZ/GmailAutomate/tree/main/config)、[HTTPX timeout](https://www.python-httpx.org/advanced/timeouts/)、[Hatchling 构建](https://hatch.pypa.io/latest/config/build/)、[Google 应用专用密码](https://support.google.com/accounts/answer/185833)。Gmail 使用具备相应账户条件的应用专用密码，不使用普通登录密码；真实认证由部署时验证。

## 2. 配置与目录约定

配置模块位于 `src/website_healthcheck/config/load_config.py`；同目录 `config.yaml` 为本地真实配置，必须忽略提交，`config.yaml.example` 仅含占位信息。对应测试为 `tests/test_load_config.py`。相对数据库和锁文件路径以配置文件所在目录为基准；示例对应 `src/website_healthcheck/config/var/`，只跟踪 `.gitkeep`，不跟踪运行数据。CLI 默认使用包内 config/config.yaml，允许 --config 显式指定；cron 显式使用绝对配置路径，启动脚本先切换仓库目录，并使用安装时确定的 uv 绝对路径。

下表是配置规则，已提供包内 config.yaml.example。带“建议”的默认值随本文审阅，其余来自已确认需求。

| 字段 | 默认值 / 约束 |
| --- | --- |
| targets | https://canlah.ai/ 和 https://canlah.ai/playbook/；非空、唯一的 HTTP(S) URL |
| check.expected_status | 200；有效 HTTP 状态码 |
| check.keyword | Canlah；非空，大小写敏感子串，直接匹配响应文本，不解析 DOM |
| check.max_response_seconds | 5；大于 0；等于阈值通过 |
| check.request_timeout_seconds | 建议 10；正数，用于 HTTPX 各阶段等待 |
| check.retries | 2；非负整数，表示首次失败后额外尝试次数 |
| check.retry_delay_seconds | 2；非负数；最后一次失败后不再等待 |
| schedule.interval_minutes | 5；初版限定为 60 的正整数因数，N<60 时生成 cron */N 分钟表达式，N=60 时生成每小时第 0 分钟表达式 |
| schedule.grace_seconds | 建议 60；大于等于 0 且小于检查周期 |
| schedule.stop_timeout_seconds | 5；正数，请求旧实例退出后最多等此时长，强制终止后再等同样时长 |
| storage.database_path | 建议 var/healthcheck.sqlite3 |
| storage.lock_path | 建议 var/healthcheck.lock；同一数据库必须共用同一锁 |
| mail.interval_minutes | 5；正整数，与检查周期独立，表示邮件尝试的最小间隔 |
| mail.host / port | smtp.gmail.com / 465；初版只实现 SMTP_SSL |
| mail.username / password | 必填，password 在模型 repr 中脱敏；示例为占位值 |
| mail.sender / recipients | 必填发件地址和非空收件地址列表 |
| mail.timeout_seconds | 建议 10；正数 |

空配置、未知字段、无效类型或边界值应明确报错，不默默使用拼写错误的配置项。错误输出只保留字段位置及安全说明，不直接打印原始配置或 Pydantic 的输入值。日志和邮件不包含密码或完整配置。真实配置文件建议权限 0600，var/ 数据不提交；数据清理/备份作为生产升级说明，初版不自动删除历史。

调度周期修改必须同时更新 cron。步骤 5 提供输出 cron 行的辅助脚本，由用户安装，不自动覆写已有 crontab。数据库保存当前周期；周期变更时以当前时间建立新基线并记录变更，不按新周期回溯生成历史漏跑。

## 3. 模块边界与接口

以下为模块接口约定；config、checker、storage 和 notifier 已合并；步骤 5 已接入完整命令流程和实际进程锁。所有时间记录使用 UTC；部署 cron 的调度时区也配置为 UTC，避免本地时区/DST 与时段计算不一致；测量耗时和重试等待使用单调时钟。

| 模块 | 接口 | 输入输出与责任 |
| --- | --- | --- |
| config | load_config(path: Path) -> Config | 读取 YAML、校验、解析路径；失败抛 ConfigError |
| checker | check_page(target, settings, client) -> CheckResult | 完成该目标的全部尝试；返回最终状态和各次尝试，不发邮件、不写库 |
| checker | run_checks(config, client) -> list[CheckResult] | 顺序检查所有目标，一个目标失败不阻断其他目标 |
| storage | reconcile_schedule(now, schedule, interrupted_run_id=None) -> list[Event] | 确认旧实例停止后，标记中断并找出历史漏跑；被终止在邮件阶段的轮也记中断，结果保留 |
| scheduler | process_lock(path, slot, stop_timeout_seconds) -> RunLock | 停止旧时段实例并取得执行锁；提供旧运行编号，登记本轮运行编号 |
| storage | start_run(slot, started_at) -> run_id | 建立 running 记录；slot 唯一，防止同一时段重复检查 |
| storage | finish_run(run_id, results, finished_at) -> None | 事务写入结果、最终运行状态和需通知事件 |
| storage | pending_report(now, mail_interval) -> Report 或 None | 查询最终故障及已确认的漏跑/中断事件，不含仍在运行的检查；附最新状态并检查发送间隔 |
| notifier | send_alert(report, mail_settings) -> None | 生成一封汇总邮件并发送；失败抛脱敏 MailError，不修改数据库 |
| storage | record_delivery(report, outcome, attempted_at) -> None | 保存通知尝试；成功才标记本报告包含的事件，失败保留 |
| main | main(argv=None) -> int | 加载配置、锁、数据库恢复检测、检查、保存、发送与退出码 |

CheckResult：目标 URL、最终成功标志、尝试列表；每次 Attempt 包含序号、UTC 开始时间、耗时秒数、最终 URL、状态码（网络错误时可空）、关键词结果（无响应时可空）及失败原因代码。失败原因可同时包含 status、latency、keyword；网络错误单独记录，不保存整页 HTML。

步骤 2 具体约定：检查实现位于 `src/website_healthcheck/checker/checker.py`，独立的 checker 包包含 `__init__.py`；用冻结 dataclass 保存 Attempt 和 CheckResult，尝试集合为 tuple；`success` 是最后一次尝试无失败原因时为真的派生属性。原因代码为 status、latency、keyword、timeout、network；HTTPX 超时单独归类 timeout，其他 RequestError（含重定向循环）归类 network，不保存底层异常消息。读取中断时不评价不完整正文，keyword_found 为 None；已取得的响应头状态码和最终 URL 可保留。Client 由调用方创建并关闭，检查函数复用它并关闭每次响应；请求显式跟随重定向并采用配置中的阶段超时。主程序入口改名为 `src/website_healthcheck/main.py`，命令映射为 `website_healthcheck.main:main`，步骤 5 已接入完整调度入口。

Report：待通知事件 ID 集合、统计区间、网站最终失败/漏跑/异常中断明细，以及各目标最新检查状态。历史故障即使随后恢复也报告；仅重试期间失败而最终恢复的结果留在检查历史，不生成故障邮件事件。

步骤 3 的返回模型位于 storage/models.py：Event 表示事件，StoredCheck 封装检查 ID、运行 ID、整轮结束时间及 CheckResult；Report 分开保存 events、failed_checks 和 latest_checks，event_ids 从事件快照派生。pending_report 的 mail_interval 以分钟计，record_delivery 的 outcome 为 success/failed，attempted_at 是发送尝试开始时刻；数据库只保存固定错误码 smtp_error，不接收原始 SMTP 异常。

步骤 4 实现位于 notifier/notifier.py：send_alert 接收 Report 和 MailSettings，以 EmailMessage 生成中文纯文本汇总，历史故障和最新状态分开展示，包含每次尝试、实际重试次数和 UTC 时间。SMTP_SSL 使用 create_default_context 校验证书与主机名；只在 login 时取出密码，不输出调试交互。部分收件人被拒收也抛 MailError，整批保留，重试可能重复发送给已接受者；连接、认证、发送及关闭错误脱敏。空事件报告不连接；邮件间隔和状态写入由存储及主程序协调，不在邮件模块内部重试或写库。详见 [邮件说明](../notifier.md)。

## 4. 状态、调度与一致性

SQLite 逻辑数据模型：

- runs：运行 ID、唯一计划时段、开始/结束时间、running/completed/interrupted 状态；网站是否健康由检查结果表示，不能与进程完成状态混淆。
- checks / attempts：关联运行与目标，保存最终判断和各次尝试。
- events：需要汇总通知的最终故障、漏跑、异常中断；保存去重键、关联运行或缺口范围，以及成功通知标记。
- deliveries：通知尝试时间、成功/失败、所包含事件的关联记录；保存脱敏错误，不保存凭证。
- schedule_state：当前周期、监控起始基线及已核对到的时段，用于漏跑幂等检测。

步骤 3 已实现 Storage(path) 上下文和全部存储接口，共七张表（含 delivery_events 关联表）；连接启用外键，使用 Python 3.12 autocommit=False 管理事务，UTC 时间显式保存为固定精度 ISO 文本。开始记录单独提交，完成事务同时保存结果和最终故障事件；失败时回滚完成事务并保留 running。恢复检测将事件、中断状态及游标一起提交，通知记录将投递、关联关系及成功标记一起提交；查询报告使用短读事务。具体字段与测试见 [存储说明](../storage.md)。

恢复检测仅核对严格超过宽限且早于当前时段的历史，当前实际即将执行时段始终排除；首次运行及周期变更以当前 UTC 时段建立基线。漏跑区间首尾均包含，以已有 runs 切分区间，不逐时段补建运行；schedule_changed 仅作审计，不单独触发故障邮件。通知报告包含所有未通知故障及各已记录目标最新保存的结果（包括邮件阶段被终止但已保存的检查结果）。步骤 5 在命令入口通过真实 flock 保证调用方独占；步骤 3 的存储测试仍不代表系统 cron 或真实 SMTP 联调成功。

执行顺序（2026-10-18 按用户审阅修改）：

1. 加载配置并计算当前 UTC 时段。尝试拿锁；旧时段实例仍在执行时，通过 PID、创建时间和打开的锁文件确认身份，发送 SIGTERM，最多等 stop_timeout_seconds，再发送 SIGKILL 并最多等同样时长。
2. 确认旧实例停止且实际拿到锁后才打开数据库，并按实际开始时刻重算当前时段，避免等待期间跨时段后误跑旧时段；无法确认身份或停止失败返回 2。同一时段、同时处理接替或自身/父辈保护返回 3，不能绕过锁并行执行。
3. 旧 running 改为 interrupted；刚终止的旧实例即使已保存检查结果、卡在邮件阶段，也改为 interrupted 并记事件，检查明细和原检查时间保留。
4. 核对过去已超过宽限、早于当前时段的应执行时间，缺少记录就记漏跑；首次部署前不追查，不补发历史请求。
5. 当前时段已有记录则返回 3；否则保存 running、登记运行编号、执行当前轮检查，统一保存结果、最终故障事件和 completed。
6. 当前结果保存后，再查询未通知事件与邮件间隔。首次事件可立即发送，无事件不发空邮件；成功或失败尝试均推进邮件间隔。
7. SMTP 成功后保存 delivery 并标记本批事件；失败保存 failed 并保留事件。整轮结束后关闭数据库、登记实例已结束并释放锁。

旧实例未结束时不再直接跳过下一时段，而是终止旧实例并执行新轮；这里不继续或重新执行旧轮。持锁者信息以 JSON 保存，更新时原子替换；另有短暂的启动锁避免两个新实例同时终止旧进程。同一数据库必须共用同一锁，本机文件系统运行。主机一直停机不能自我告警，恢复后才发现漏跑。SMTP 已接收但数据库提交前退出可能重复邮件，不承诺严格一次投递。详见 [命令与调度说明](../scheduling.md)。

CLI 退出码：0=本轮健康且没有执行错误；1=至少一个目标重试后仍失败；2=配置、存储或邮件执行错误；3=同一时段重复启动、另一个新实例正在接替或自身/父辈保护。执行错误优先于网站失败；历史漏跑已记录并不使当前健康检查本身失败。scripts/run_healthcheck.sh 使用 uv 绝对路径和 --no-sync，不在每轮同步环境，exec 保留退出码；scripts/print_cron.py 从配置生成 cron 行并在该行追加日志重定向，不安装任务。Cronie 使用 CRON_TZ=UTC；不支持此变量的 cron 要求守护进程使用 UTC。路径支持空格与引号，但拒绝百分号和换行；日志轮转及安装步骤见 [调度说明](../scheduling.md)。

## 5. pytest 验收与发布

统一使用 uv run --locked pytest。pytest 属于 dev 依赖；每个实现任务同步添加测试。fixture、tmp_path、临时 SQLite 和 monkeypatch 隔离环境；单元测试使用 HTTPX MockTransport 及 sleep/时钟替身，本机 HTTP 集成使用真实连接、计时和延迟；SMTP 使用替身，不在 CI 真实发信或请求外网站点。

| 范围 | 必测场景 |
| --- | --- |
| 配置 | 缺文件/空文档/非法 YAML/未知字段、类型与边界错误、相对路径、示例有效及密码不泄露 |
| HTTP | 成功、重定向、非200、缺词、大小写、5秒边界、耗时包含响应体、各类网络错误、多项失败、重试恢复/耗尽、最后不sleep |
| 存储 | 首次基线、正常完成、临时数据库重启、事务回滚、失败原因保存、重复时段和重复事件去重 |
| 漏跑 | 多时段缺口、宽限边界、上次中断、长任务重叠、周期变更、首次启动不补历史、无历史请求补跑 |
| 邮件 | 无事件、间隔未到/到期、首次故障、故障后恢复仍汇报、失败不标记、成功只标记本批、凭证脱敏 |
| 集成 | 本机 HTTP 服务验证响应和延迟；子进程 CLI 退出码、不同工作目录、锁互斥、全流程与 cron 行生成 |
| 发布 | CI 不连接真实服务；版本与标签匹配，测试失败阻断发布，重复发布不覆盖已有资产 |

Linux CI 必须运行完整 pytest；Mac 本地执行同一套测试。真实 HTTP 和 Gmail 冒烟验证需独立执行并记录时间、命令、范围及结果，不把 mock 成功当成真实集成成功。步骤 1 已有 59 项配置和入口测试；步骤 2 新增 36 项离线检查测试，本地合计 95 项 pytest 通过。HTTPX MockTransport 与可控时钟验证请求处理、完整正文及重定向耗时、边界和重试；当时超时异常由测试模拟，不代表真实网络超时验证。步骤 6 已补充本机 HTTP 连接并将 workflow 改为完整 pytest，远端执行结果须在提交推送后核对。

步骤 3 新增 47 项真实临时 SQLite 测试，本地合计 142 项通过，覆盖生命周期、结果及重试历史、唯一约束、重开、首次基线、漏跑区间、宽限边界、中断、长任务重叠导致的跳过、周期变化，以及通知间隔/快照/成功标记/失败保留。通过触发器故意中断写入，验证结果保存、恢复游标及通知状态的事务回滚。未运行真实 HTTP、SMTP 或 cron 集成。

步骤 4 新增 19 项离线邮件测试，本地合计 161 项通过；SMTP 使用替身，验证中文 MIME、汇总内容、配置传递、TLS 上下文、异常/部分拒收脱敏、无内部重试，以及临时 SQLite 中的间隔、历史恢复和通知快照边界。未连接真实 Gmail；此为步骤 4 当时的范围，步骤 5 已接入入口和实际进程锁。

步骤 5 重写入口测试并新增锁与 cron 测试；本轮审阅新增 psutil 及终止旧实例策略后，相关 74 项、本地全套 240 项通过。真实 SQLite/flock 配合 HTTP、SMTP 和时钟替身验证主流程；子进程和 shell 验证跨目录、互斥、终止与强制结束后接替、进程身份保护、邮件阶段中断保留结果、日志和退出码。sdist/wheel 及运行数据排除检查通过；未安装系统 cron 或连接真实外部服务。详见 [命令与调度说明](../scheduling.md)。

步骤 6 新增 tests/test_http_integration.py：本机回环 HTTP 服务验证重定向、完整正文耗时、实际读取超时、连接失败、协议错误和重试；主流程配合真实 SQLite、邮件生成及 SMTP 替身，验证通知失败保留和恢复后汇总。CI 的 Ubuntu job 使用 Python 3.12、uv 0.9.28、uv sync --locked 和 uv run --locked pytest，无 Gmail Secrets。补充 10 行英文 README、双语项目说明、部署维护与测试说明；用户于 2026-10-18 确认 Mac 本地真实测试通过；该结果单独记录为用户反馈，不代表 Linux 生产主机已验收。

步骤 7 增加 v* 标签触发的 Actions，检出标签、uv sync --locked、运行 pytest，核对标签版本等于 pyproject.toml version，并确认提交属于已合并 main，再以发布 job 的 contents: write 权限创建 Release 和自动生成说明。重复执行时已有 Release 则跳过创建。PR #19 合并并获得用户授权后，已为 76ada0b 推送 v0.1.0；实际 Linux Release workflow 的 271 项 pytest 通过，随后创建正式 Release。详细操作与证据见 [发布说明](../releases.md#v010-正式发布记录)。

初版历史交付中，README 保持 5–10 行，集中回答每五分钟运行、故障告警和生产前补充；根目录 PROJECT_GUIDE.md 和 PROJECT_GUIDE_CN.md 另行介绍功能、技术栈、模块、运行逻辑、安装配置和部署，并逐项解释生产准备建议。详细说明现已整合为[英文 README](../../README.md) 和[中文 README](../../README_CN.md)，原行数限制不再适用。详细 cron、Gmail 配置、SQLite 维护及验证证据继续由 docs/ 文档维护。升级方案包括独立心跳监控、专业调度平台、告警抑制/升级策略、凭证管理和数据保留策略。

## 6. 实施顺序与审阅

以根目录 [TODO](../../TODO.md) 的 Issue 链接为准。步骤 0 文档审阅合并后，按 1 配置 → 2 检查 → 3 存储 → 4 邮件 → 5 集成调度 → 6 验收文档 → 7 发布逐项推进。每一步独立分支、Issue、PR；不提前实施后续任务。后续技术细节有变动先更新设计并与用户确认。

初版全部步骤已完成。公开前的文档与 [MIT 许可证](../../LICENSE) 收尾已通过 Issue #20、PR #21 合并，署名 2026 ACienZ，包元数据与双语 README 已同步。旧 v0.1.0 快照没有许可证，因此 [Issue #22](https://github.com/ACienZ/website-healthcheck/issues/22) 准备 v0.1.1：版本与锁文件同步，合并后再推送新标签，实际发布状态以 [发布说明](../releases.md#v011-发布准备) 中的记录及 GitHub Release 为准。


## 7. GitHub 展示定位调整（2026-10-29）

v0.1.1 已于 2026-10-18 正式发布，本轮已回读 Release 核实；上一节的版本准备说明保留为历史记录。

用户确认将项目定位为“Python 网站健康检查练习 / Python Website Healthcheck Exercise”，面向浏览 GitHub 的读者介绍能力、运行方式和工程实践。现行介绍移除求职申请语境；原始题目及开发验收记录保留为历史资料。默认 Canlah 目标如实标为原始练习示例。

- 将两份 PROJECT_GUIDE 的完整技术内容分别整合到 README.md / README_CN.md，移除旧指南，统一语言切换、目录树及文档链接；不再受 5–10 行限制。
- 保留功能、技术栈、模块职责、流程、配置、部署和维护说明；安装后先通过 pytest 与 --help 体验，真实网站请求、邮件与 cron 部署另行说明。
- 突出 HTTP 重试、SQLite 状态记录、漏跑/中断检测、汇总通知及隔离测试，明确单机练习的生产边界。仓库、包、命令、默认目标、版本、业务代码和接口保持不变。
- 本轮只检查文档链接、章节锚点、双语命令、完整 diff 和空白错误；不新增测试、不运行真实网站、Gmail 或 cron。既有 271 项测试结果保留日期并标为历史证据。
- 任务为 [Issue #24](https://github.com/ACienZ/website-healthcheck/issues/24)，关联分支 `codex/24-showcase-readme`，Project #12 使用 documentation / P1 / S / In progress；用户已于 2026-10-29 审阅本地文档并授权提交、推送和创建 PR；PR 由用户 review 后合并。


## 8. 完整改名与 v0.2.0 准备（2026-10-29）

用户确认新名称为 website-healthcheck，范围覆盖 GitHub 仓库、Project 展示名、分发包、Python 包、命令、启动脚本、通知标题及现行文档。GitHub 仓库为 ACienZ/website-healthcheck，Project #12 更名为 Website Healthcheck Tracker，原可见性保持不变。

- 分发包与命令使用 `website-healthcheck`，源码为 `src/website_healthcheck/`，导入为 `website_healthcheck`。旧包和命令名不再作为当前入口；旧发布标签保留原内容，不重写历史。
- 名称变化影响安装及导入入口，因此本项目版本由 0.1.1 更新到 0.2.0，依赖版本保持不变。HTTP 默认 Canlah 目标/关键词、数据库结构和调度规则保持不变。
- 本地真实配置与 var 目录随源码目录迁移，原文件不改写。部署迁移先暂停本项目旧 cron 并确认实例退出，再迁移配置/数据、运行 uv sync --locked 并更新命令与 cron 路径；不自动读取凭证或改写 crontab。
- 验收包括完整 pytest、新命令 --help、Python 包导入、shell 语法、文档链接及双语命令、sdist/wheel 内容和独立安装检查。测试继续使用临时数据、回环 HTTP 和 SMTP 替身。
- 关联 [Issue #26](https://github.com/ACienZ/website-healthcheck/issues/26) 与分支 `codex/26-rename-website-healthcheck`，Project 为 P1 / M / In progress。用户审阅合并后，整理本地分支与目录，再按已授权目标发布并核验 v0.2.0。
