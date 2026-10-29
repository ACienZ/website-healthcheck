"""定义“整轮运行 → 页面结果 → 每次请求 → 待通知事件”的数据库结构。

先区分三个层级，避免把“运行次数”和“重试次数”混在一起：
    runs：一次定时启动为一轮，例如 12:00 启动，检查两个页面。
    checks：这一轮中，一个页面对应一条最终结果，因此通常有两条。
    attempts：某个页面的每次 HTTP 尝试，包括首次请求和后续重试。
    events：需要日后汇总通知的故障，不是所有检查历史的副本。

关联关系：runs.id → checks.run_id，checks.id → attempts.check_id。
events 通过 run_id/check_id 关联故障来源；正常结果仍保存在前面三张表。

例如首页“失败 → 失败 → 成功”：1 条 check、3 条 attempt、0 条故障 event。
这意味着实际重试了 2 次；配置中的 retries=2 是最多允许额外重试 2 次，
不意味着每次检查都重试两次。当前保存实际尝试历史，不另存配置上限快照。

这里只声明建表语句；导入本模块不会创建文件或连接数据库。
执行建表由 Storage.__init__ 负责。schedule_state 保存漏跑检测进度；
deliveries / delivery_events 保存通知尝试及其包含的事件，实际发信由邮件模块负责。
"""

# 每个字符串是一条 SQL，通过 execute 逐条放进同一个建表事务。
# IF NOT EXISTS 允许重开数据库；它不会迁移已有表，初版暂不引入迁移工具。
SCHEMA = (
    # 1. runs：记录“这一轮程序有没有开始、有没有完整结束”。
    # id：SQLite 自动分配的运行主键，供检查结果和事件引用。
    # slot：计划启动时段，例如 12:00；UNIQUE 防止同一时段重复运行。
    # started_at：实际开始时间，例如 12:00:03，与计划时间分开保存。
    # finished_at：检查结果保存时间；若检查未完成就中断，则记录发现时间。running 时为 NULL。
    # status：running=开始但尚未保存结果；completed=网页结果已保存；
    #         interrupted=旧轮中途退出或被下一轮终止（可能已保存网页结果）。
    # 网站失败也可 completed；它表示检查结果已保存，不保证邮件阶段也已完成。
    # 最后的 CHECK 约束保证状态与 finished_at 一致，拒绝矛盾记录。
    """
    CREATE TABLE IF NOT EXISTS runs (
        id INTEGER PRIMARY KEY,
        slot TEXT NOT NULL UNIQUE,
        started_at TEXT NOT NULL,
        finished_at TEXT,
        status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'interrupted')),
        CHECK ((status = 'running' AND finished_at IS NULL)
            OR (status != 'running' AND finished_at IS NOT NULL))
    )
    """,
    # 2. checks：保存“一轮中某个页面重试完毕后的最终结论”。
    # id：该页面本轮检查的主键；run_id：所属 runs.id，外键禁止悬空引用。
    # target：原始目标 URL；success：最终成功为 1、最终失败为 0。
    # UNIQUE(run_id, target)：同一轮同一目标只保存一次，不为重试新增 check。
    # 此表只存最终摘要，具体每次请求是否失败、耗时多久，查看 attempts。
    """
    CREATE TABLE IF NOT EXISTS checks (
        id INTEGER PRIMARY KEY,
        run_id INTEGER NOT NULL REFERENCES runs(id),
        target TEXT NOT NULL,
        success INTEGER NOT NULL CHECK (success IN (0, 1)),
        UNIQUE (run_id, target)
    )
    """,
    # 3. attempts：保存“某个页面实际发出的每次尝试”，包含失败和成功历史。
    # id：尝试主键；check_id：所属 checks.id。
    # number：从 1 开始，1 是首次请求，2 是第一次重试，3 是第二次重试。
    #         按 check_id 统计 COUNT(*) - 1，即该页面本轮实际重试次数。
    #         checker 生成连续序号；数据库限制序号为正且同一 check 内唯一。
    # started_at / elapsed_seconds：本次请求的 UTC 开始时间与完整响应耗时。
    # final_url：重定向后的 URL；status_code：响应状态码；未取得时为 NULL。
    # keyword_found：完整正文含关键词为 1、不含为 0；未读完正文为 NULL。
    # failure_codes：JSON 数组文本，例如 ["status", "keyword"]；成功为 []。
    # 不保存完整 HTML 或底层异常消息；耗时不包括两次尝试之间的等待。
    """
    CREATE TABLE IF NOT EXISTS attempts (
        id INTEGER PRIMARY KEY,
        check_id INTEGER NOT NULL REFERENCES checks(id),
        number INTEGER NOT NULL CHECK (number > 0),
        started_at TEXT NOT NULL,
        elapsed_seconds REAL NOT NULL CHECK (elapsed_seconds >= 0),
        final_url TEXT,
        status_code INTEGER,
        keyword_found INTEGER CHECK (keyword_found IN (0, 1)),
        failure_codes TEXT NOT NULL,
        UNIQUE (check_id, number)
    )
    """,
    # 4. events：将“需要通知的事情”与“全部检查历史”分开。
    # kind：check_failed=页面最终失败；missed=漏跑；interrupted=上次中断；
    #       schedule_changed=调度周期改变，仅留审计记录，不单独触发邮件。
    # dedupe_key：事件唯一键；页面失败使用 check_failed:<check_id>，防止重复。
    # occurred_at：记录事件的 UTC 时间，页面最终失败使用本轮结束时间。
    # run_id / check_id：关联来源；漏跑没有对应运行/页面，因而允许为空。
    # range_start / range_end：连续漏跑的首尾计划时段，两端均包含。
    # notified_at：成功通知时间；NULL 表示尚未通知，发送成功后才填写。
    # 重试后恢复不创建事件，但失败尝试仍在 attempts 中，可供追溯。
    # 注意：此表只记录待通知事项，创建一条事件不等于已经发送邮件。
    """
    CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY,
        kind TEXT NOT NULL CHECK (kind IN ('check_failed', 'missed', 'interrupted', 'schedule_changed')),
        dedupe_key TEXT NOT NULL UNIQUE,
        occurred_at TEXT NOT NULL,
        run_id INTEGER REFERENCES runs(id),
        check_id INTEGER REFERENCES checks(id),
        range_start TEXT,
        range_end TEXT,
        notified_at TEXT
    )
    """,
    # 5. schedule_state：只有 id=1 一行，保存当前调度周期与检测游标。
    # baseline_slot：首次启用或周期变化时建立的起点，不追溯此前漏跑。
    # next_slot：下次从哪个计划时段继续核对；已核对区间不再重复生成事件。
    # 游标更新和漏跑/中断事件同事务提交，崩溃时一起回滚，避免漏记或重记。
    """
    CREATE TABLE IF NOT EXISTS schedule_state (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        interval_minutes INTEGER NOT NULL CHECK (interval_minutes > 0),
        baseline_slot TEXT NOT NULL,
        next_slot TEXT NOT NULL
    )
    """,
    # 6. deliveries：一次 SMTP 尝试对应一行，成功和失败都会记录时间。
    # attempted_at 用于下一次发信的最小间隔；失败不应导致每轮高频重发。
    # error_code 只保存固定分类 smtp_error，不接受底层异常或 SMTP 凭证原文。
    """
    CREATE TABLE IF NOT EXISTS deliveries (
        id INTEGER PRIMARY KEY,
        attempted_at TEXT NOT NULL,
        outcome TEXT NOT NULL CHECK (outcome IN ('success', 'failed')),
        error_code TEXT
    )
    """,
    # 7. delivery_events：一封汇总邮件可含多个事件，同一事件也可能尝试多次。
    # 因而用关联表表达多对多关系；成功只标记本报告的事件，不误标后来的故障。
    """
    CREATE TABLE IF NOT EXISTS delivery_events (
        delivery_id INTEGER NOT NULL REFERENCES deliveries(id),
        event_id INTEGER NOT NULL REFERENCES events(id),
        PRIMARY KEY (delivery_id, event_id)
    )
    """,
)
