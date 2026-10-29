"""存储接口返回的数据摘要，供未来邮件模块读取，不含连接或发送操作。"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from website_healthcheck.checker.checker import CheckResult

EventKind = Literal["check_failed", "missed", "interrupted", "schedule_changed"]
DeliveryOutcome = Literal["success", "failed"]


@dataclass(frozen=True)
class Event:
    """已持久化的事件；漏跑区间的首尾时段均包含在内。

    occurred_at 是故障保存或恢复检测时刻，中断时不能将它误作原进程退出时刻。
    run_id/check_id 标识原记录；漏跑无对应执行，二者可为空。
    range_start/range_end 仅用于漏跑事件，其他类型为空。
    """

    id: int
    kind: EventKind
    occurred_at: datetime
    run_id: int | None
    check_id: int | None
    range_start: datetime | None
    range_end: datetime | None


@dataclass(frozen=True)
class StoredCheck:
    """一个已完成页面检查及其数据库标识、整轮结束时间。"""

    check_id: int
    run_id: int
    finished_at: datetime
    result: CheckResult


@dataclass(frozen=True)
class Report:
    """一次待发送的报告快照；历史故障与各目标最新状态分别保留。

    events 是本次要通知的故障/漏跑/中断；failed_checks 对应历史故障详情，
    即使目标后来恢复也不删除。latest_checks 让邮件同时说明当前是否已恢复。
    period_start 取本批最早事件或漏跑起点，period_end 是生成报告时刻。
    报告生成不会标记已通知；只有 SMTP 成功后的 record_delivery 才标记。
    """

    events: tuple[Event, ...]
    failed_checks: tuple[StoredCheck, ...]
    latest_checks: tuple[StoredCheck, ...]
    period_start: datetime
    period_end: datetime

    @property
    def event_ids(self) -> tuple[int, ...]:
        """从快照提取本批事件 ID，供投递事务精确关联与标记。"""
        return tuple(event.id for event in self.events)
