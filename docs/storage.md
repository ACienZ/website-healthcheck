# SQLite 执行与结果记录

## 当前进度

任务 3 对应 Issue #7、分支 `codex/7-sqlite-execution-records`，按三段实现和审阅：

1. 数据库、运行开始和结果原子保存：已实现。
2. 漏跑区间、首次基线、异常中断和周期变更：已实现。
3. 通知状态查询和记录：已实现；实际 SMTP 发送属于任务 4。

模块位于 `src/website_healthcheck/storage/`：`schema.py` 定义七张表，`models.py` 定义 Event/StoredCheck/Report，`storage.py` 管理连接及读写，`__init__.py` 标识包。先读 Storage 类说明，再看各方法的编号步骤及 schema 中的表/字段说明。标准库 sqlite3 随 Python 提供，无新增 uv 依赖。

Storage 使用类来让各个读写方法共用同一连接，并统一负责连接配置、事务和关闭。它接收 checker 已生成的结果，不执行 HTTP 或重试。处理顺序是：提交 running → 调用方检查页面 → 保存各页面最终结果 → 保存每次尝试 → 为最终失败创建事件 → 标记 completed 并统一提交。

步骤 5 的主入口已接入数据库和实际进程锁，具体顺序见 [命令与调度说明](scheduling.md)。实际流程先独占进程锁，然后调用 reconcile_schedule，之后才 start_run，不能在活跃运行中调用恢复检测，否则会把当前运行误记为中断。

## 如何判断过去发生了什么

数据库里的 running 只表示“写下了开始记录，还没保存完成结果”，不能单凭它判断进程仍活着。主程序须先确认旧实例已经停止，然后才能调用 reconcile_schedule 检查过去的记录。

- 旧记录仍是 running：把它改为 interrupted，并记一条待通知事件；不会继续或重新执行旧任务。
- 记录为 completed：页面检查结果已经保存；网站可能不健康，之后的邮件也可能失败。若主入口明确传入刚终止的旧轮编号，函数也会将该轮改为 interrupted；保留检查时间与明细，中断时间记在事件中。
- 应执行的历史时段没有启动记录：超过宽限后记 missed；部署前和当前时段不算历史漏跑。

reconcile_schedule 的五段代码依次做：标记没跑完的旧任务 → 确定从哪里开始查 → 确定这次查到哪里 → 找出缺少启动记录的时段 → 保存已核对到的位置。这个函数不会发 HTTP 请求。

## 已实现接口

| 接口 | 行为 |
| --- | --- |
| `with Storage(path) as store` | 创建数据库父目录、打开连接、建表；退出时关闭连接 |
| `store.start_run(slot, started_at) -> int` | 保存并提交 running，返回运行 ID；计划时段重复抛 DuplicateRunError |
| `store.finish_run(run_id, results, finished_at)` | 同一事务保存整轮检查、全部尝试、最终故障事件，并将运行标记 completed |
| `store.reconcile_schedule(now, schedule, interrupted_run_id=None) -> list[Event]` | 给未完成或刚被终止的旧轮记中断、查历史漏跑、处理周期变更；返回本次新建事件 |
| `store.pending_report(now, mail_interval) -> Report 或 None` | 当前轮完成且邮件间隔到期时，查询待通知快照；间隔单位为分钟 |
| `store.record_delivery(report, outcome, attempted_at)` | 记录 success/failed 投递结果，成功才标记本报告事件；不发送邮件 |

所有时间必须带时区，写入时统一为固定微秒精度的 UTC ISO 文本。slot 必须对齐分钟，开始时间不得早于 slot；结束时间不得早于开始时间。SQLite 不直接保存 Python datetime 对象，避免默认转换器丢失时区信息。

completed 表示本轮网页检查结果已保存，后续邮件可能尚未结束；它与网站是否健康分开：存在最终失败时依然是 completed，由 checks.success 和事件表达故障。恢复的目标保留重试失败历史，但不产生最终故障事件。

## 表结构与事务

| 表 | 当前职责与约束 |
| --- | --- |
| runs | 唯一 slot、开始/结束时间、running/completed/interrupted 状态 |
| checks | 每轮每目标一条最终结果，`(run_id, target)` 唯一，引用 runs |
| attempts | 每次尝试的序号、UTC 时间、耗时、响应摘要、失败原因；`(check_id, number)` 唯一 |
| events | 最终失败、漏跑、中断和周期变更事件；唯一 dedupe_key，关联运行/检查或漏跑区间，notified_at 默认空 |
| schedule_state | id=1 的单行状态：当前周期、起始基线、下次待核对时段 |
| deliveries | 每次通知尝试的时间、success/failed 和固定错误代码 smtp_error |
| delivery_events | 关联投递与事件，支持一封邮件多个事件、一个事件多次尝试 |

布尔值存为 0/1，未取得的响应字段存为 NULL。失败原因用 JSON 数组保留多个代码，不保存 HTML 或原始异常消息。所有业务值均使用 SQL `?` 占位符绑定。

## 重试次数如何记录

记录了实际尝试历史，没有单独的 retry_count 字段。一个页面本轮只有一条 checks，但可有多条 attempts：number=1 是首次请求，number=2 是第一次重试，number=3 是第二次重试。按 check_id 统计 attempts 的条数减 1，就是实际重试次数。

例如“失败 → 失败 → 成功”对应一条最终 success=1 的 check、三条 attempt，实际重试两次，没有待通知故障事件。若三次都失败，仍只有一条 check 和三条 attempt，但 success=0，并产生一条 check_failed 事件。

配置中的 check.retries 表示最多允许的额外尝试次数；实际次数可能更少。当前保存每次实际尝试的状态与原因，没有保存当时最大重试配置的快照。

## 连接与提交边界

连接先在事务外启用外键，再使用 Python 3.12 的 `autocommit=False` 模式。每个写入方法用 `with connection` 控制提交和回滚；这个上下文不会关闭连接，关闭由 Storage 的外层上下文负责。参考 [Python 事务控制](https://docs.python.org/3.12/library/sqlite3.html#transaction-control-via-the-autocommit-attribute) 和 [SQLite 外键说明](https://www.sqlite.org/foreignkeys.html)。

start_run 单独提交，确保进程在请求网站期间退出时仍留下 running。finish_run 内部若任意检查、尝试或事件写入失败，整个完成事务回滚，之前提交的 running 保留；不能残留半轮结果。数据库重复打开使用 IF NOT EXISTS 保留已有内容，当前未引入表迁移工具。

同一运行重复完成会被拒绝，不重复生成结果或事件。底层 SQLite 错误转换为不含 SQL 参数和路径的 StorageError；调用方参数错误使用 ValueError，不静默生成不完整结果。

## 漏跑与中断如何识别

`reconcile_schedule` 必须在取得锁后、当前运行开始前调用。遗留 running 说明之前的持锁进程已退出，将其标为 interrupted 并创建事件；它记录的是恢复确认时间，不推测精确退出时刻。函数本身不按运行时长杀进程；步骤 5 的入口先终止仍占用旧时段的实例，再调用它记中断。

首次核对仅以当前 UTC 计划时段建立基线，不追溯部署前的历史。后续从 schedule_state.next_slot 开始，核对严格超过 grace_seconds 且早于当前时段的计划启动时间；当前实际即将执行的时段始终排除，即使启动已晚于宽限，也不会被本轮自己记成漏跑。

按已有 runs 将缺失时段分成连续区间，首尾均包含。例如应有 00/05/10/15 分钟、实际有 00/10，则保存 [05,05] 和 [15,15] 两个事件。长时间停机只生成连续区间，不逐时段补建运行或发 HTTP 请求。已有 interrupted 仍代表那一时段确实启动过，不再重复记为漏跑。

事件和检测游标同事务提交。重复核对、进程重开不会重记已核对区间；事务失败时游标、中断状态和事件全部回滚。周期变更时重新建立当前时段基线，保存 schedule_changed 审计事件，不按新周期回溯旧历史；该审计事件本身不触发故障邮件。周期变化仍须同时修改 cron，后续部署文档说明。

主机持续停机期间不能自己发送告警；需要下一次启动后才能识别。这是任务 3 当时的存储边界；步骤 5 的实际终止、拿锁和新轮执行见 [调度说明](scheduling.md)。

## 报告与通知状态

有 running 记录时不生成报告；无故障事件或间隔未到时也返回 None。成功或失败的上次通知尝试均推进最小邮件间隔。首次故障没有既往尝试，可在当前轮完成后立即生成报告；不会修改任何通知标记。

Report 固定本次 event_ids，同时包含历史失败详情 failed_checks、各已记录目标最新保存的结果 latest_checks（包括检查已保存、邮件阶段被终止的轮），以及统计区间。旧故障即使后来恢复也保留，不能只从最新失败状态推导待通知事件。查询返回前结束读事务，避免 SMTP 等待期间占用数据库读锁。

邮件模块将调用 record_delivery(report, outcome, attempted_at)，outcome 仅接受 success/failed，attempted_at 为发送尝试开始时刻。成功将本报告的事件标记 notified_at；报告生成后新增的事件保持未通知。失败只记投递历史，事件留待下一次；底层 SMTP 异常原文和凭证不传入数据库，只保存固定分类 smtp_error。

投递记录、投递与事件的关联、成功通知标记同事务提交；失败则全部回滚。重复提交已经通知成功的报告会被拒绝。SMTP 已接收而数据库提交前发生崩溃时可能重复发信，这个边界仍然存在，本实现不承诺恰好一次通知。

## 验证记录

2026-10-18，本机 Python 3.12：`uv run --locked pytest -q` 共 142 项通过，其中任务 3 新增 47 项存储测试。`tests/test_storage.py` 使用 tmp_path 创建真实临时 SQLite 文件，并以独立连接读取提交后的内容，覆盖生命周期、结果和重试记录、唯一约束、重开、事务回滚与参数绑定，以及首次基线、宽限边界、漏跑区间、恢复中断、长任务跳过时段、周期变化、通知快照、间隔与投递状态一致性。

以上为任务 3 当时的验证记录：没有创建项目 config/var 下的数据库，没有访问 HTTP 或 SMTP，未进行真实 cron/进程锁或邮件联调。任务 3 已合并；步骤 5 追加 2 项存储测试，验证显式终止的旧轮只记一次中断、保留已保存的检查时间和明细，当时全套为 240 项。步骤 6 补充本机 HTTP 与存储的集成，并将 workflow 改为完整 pytest；后续证据见 [统一测试说明](testing.md)，数据库查询和备份见 [部署说明](deployment.md)。

`uv build --out-dir /tmp/canlah-task3-dist` 构建 sdist/wheel 成功；归档检查确认包含 storage 的 schema/models/读写模块，未包含真实 config.yaml 或 SQLite 数据库。本地文档链接及 diff 空白检查通过。
