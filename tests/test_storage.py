"""使用真实的临时 SQLite 文件验证存储、恢复检测和通知状态。

运行：uv run --locked pytest tests/test_storage.py -v
tmp_path 每项测试提供独立目录；用第二个连接读取磁盘，验证提交而非仅内存状态。
时刻由测试显式传入，无须真实等待 cron 或连接 SMTP。
"""

from contextlib import closing
from datetime import UTC, datetime, timedelta, timezone
import json
import sqlite3

import pytest

from website_healthcheck.checker.checker import Attempt, CheckResult
from website_healthcheck.config.load_config import ScheduleSettings
from website_healthcheck.storage.storage import DuplicateRunError, Storage, StorageError

SLOT = datetime(2026, 10, 18, 0, 0, tzinfo=UTC)
FINISHED = SLOT + timedelta(seconds=30)


@pytest.fixture
def database(tmp_path):
    """只提供路径，让被测代码负责创建 var 目录与数据库。"""
    return tmp_path / "var" / "healthcheck.sqlite3"


def read(database, sql, parameters=()):
    """从独立连接查询已提交的数据，读完后关闭连接。"""
    # sqlite3.Connection 的 with 只管理事务，不自动关闭连接。
    # closing 是标准库的关闭上下文，确保测试不会留下文件句柄。
    with closing(sqlite3.connect(database)) as connection:
        connection.row_factory = sqlite3.Row
        # Row 支持 row['status']，比记忆 SELECT 各列下标更容易读。
        return connection.execute(sql, parameters).fetchall()


def result(target="https://example.com/", failures=((),), started_at=SLOT):
    """构造检查模块返回的数据；每个原因元组代表一次尝试。"""
    return CheckResult(target=target, attempts=tuple(
        Attempt(
            number=number, started_at=started_at + timedelta(seconds=number),
            elapsed_seconds=0.25, final_url=target, status_code=200,
            keyword_found="keyword" not in codes, failure_codes=codes,
        )
        for number, codes in enumerate(failures, start=1)
    ))


def test_initialization_and_reopen(database):
    assert not database.parent.exists()
    with Storage(database):
        tables = read(database, "SELECT name FROM sqlite_master WHERE type = 'table'")
        assert {row['name'] for row in tables} == {
            "runs", "checks", "attempts", "events", "schedule_state",
            "deliveries", "delivery_events",
        }
    # 重开不应删除既有表或报“表已存在”。
    with Storage(database) as store:
        run_id = store.start_run(SLOT, SLOT)
    with Storage(database):
        assert read(database, "SELECT id FROM runs")[0]["id"] == run_id


def test_running_is_committed_before_checks(database):
    with Storage(database) as store:
        store.start_run(SLOT, SLOT + timedelta(seconds=2))
        row = read(database, "SELECT * FROM runs")[0]
        assert row["status"] == "running"
        assert row["finished_at"] is None
        assert read(database, "SELECT * FROM checks") == []


def test_finish_preserves_attempts_and_final_failure(database):
    recovered = result(failures=(("timeout",), ()))
    failed = result("https://example.com/other", (("status", "latency", "keyword"),))
    with Storage(database) as store:
        run_id = store.start_run(SLOT, SLOT)
        store.finish_run(run_id, [recovered, failed], FINISHED)

    run = read(database, "SELECT * FROM runs")[0]
    assert run["status"] == "completed"  # 网站故障也属于已完成的一轮。
    assert datetime.fromisoformat(run["finished_at"]) == FINISHED
    checks = read(database, "SELECT * FROM checks ORDER BY id")
    assert [row["success"] for row in checks] == [1, 0]
    attempts = read(database, "SELECT * FROM attempts ORDER BY id")
    assert len(attempts) == 3
    assert [json.loads(row["failure_codes"]) for row in attempts] == [
        ["timeout"], [], ["status", "latency", "keyword"],
    ]
    assert attempts[0]["elapsed_seconds"] == 0.25
    assert datetime.fromisoformat(attempts[0]["started_at"]) == SLOT + timedelta(seconds=1)
    events = read(database, "SELECT * FROM events")
    assert len(events) == 1  # 恢复的目标不产生故障事件。
    assert events[0]["kind"] == "check_failed"
    assert events[0]["check_id"] == checks[1]["id"]
    assert events[0]["run_id"] == run_id
    assert events[0]["notified_at"] is None


def test_healthy_run_has_no_event(database):
    with Storage(database) as store:
        run_id = store.start_run(SLOT, SLOT)
        store.finish_run(run_id, [result()], FINISHED)
    assert read(database, "SELECT * FROM events") == []


def test_absent_response_fields_remain_null(database):
    attempt = Attempt(1, SLOT, 1.0, None, None, None, ("network",))
    with Storage(database) as store:
        run_id = store.start_run(SLOT, SLOT)
        store.finish_run(run_id, [CheckResult("https://example.com/", (attempt,))], FINISHED)
    row = read(database, "SELECT * FROM attempts")[0]
    assert row["final_url"] is None
    assert row["status_code"] is None
    assert row["keyword_found"] is None


def test_duplicate_slot_is_rejected_without_changing_existing_run(database):
    with Storage(database) as store:
        original_id = store.start_run(SLOT, SLOT)
        with pytest.raises(DuplicateRunError):
            store.start_run(SLOT, SLOT + timedelta(seconds=10))
    assert len(read(database, "SELECT * FROM runs")) == 1
    assert read(database, "SELECT * FROM runs")[0]["id"] == original_id


def test_finish_twice_is_rejected(database):
    with Storage(database) as store:
        run_id = store.start_run(SLOT, SLOT)
        store.finish_run(run_id, [result(failures=(("keyword",),))], FINISHED)
        with pytest.raises(StorageError, match="正在运行"):
            store.finish_run(run_id, [result()], FINISHED)
    assert len(read(database, "SELECT * FROM checks")) == 1
    assert len(read(database, "SELECT * FROM events")) == 1


def test_unknown_run_cannot_be_finished(database):
    with Storage(database) as store:
        with pytest.raises(StorageError, match="正在运行"):
            store.finish_run(999, [result()], FINISHED)
    assert read(database, "SELECT * FROM runs") == []


def test_partial_save_rolls_back_everything_except_original_running(database):
    """故意让第二个目标重复，触发真实 SQL 约束错误，验证整轮原子性。"""
    with Storage(database) as store:
        run_id = store.start_run(SLOT, SLOT)
        failed = result(failures=(("keyword",),))
        # 第一条结果的检查、尝试和事件已经插入，第二条才触发 UNIQUE。
        # 因而这个测试能发现“逐条提交导致残留半轮数据”的实现错误。
        with pytest.raises(StorageError, match="无法保存"):
            store.finish_run(run_id, [failed, failed], FINISHED)
        assert read(database, "SELECT * FROM checks") == []
        assert read(database, "SELECT * FROM attempts") == []
        assert read(database, "SELECT * FROM events") == []
        assert read(database, "SELECT status FROM runs")[0]["status"] == "running"
        # 回滚后连接仍可用，同一轮可重新正确保存。
        store.finish_run(run_id, [result()], FINISHED)
    assert read(database, "SELECT status FROM runs")[0]["status"] == "completed"


def test_foreign_keys_are_enabled(database):
    """sqlite 的外键默认可能关闭，要在实际存储连接上验证约束有效。"""
    with Storage(database) as store:
        # 这里直接访问内部连接仅为测试数据库约束，业务调用使用公开方法。
        with pytest.raises(sqlite3.IntegrityError):
            with store._connection:
                store._connection.execute(
                    "INSERT INTO checks (run_id, target, success) VALUES (999, 'missing', 1)",
                )


def test_normalize_timezone_and_reject_equivalent_duplicate_slot(database):
    local_slot = SLOT.astimezone(timezone(timedelta(hours=9)))
    with Storage(database) as store:
        store.start_run(local_slot, local_slot)
        with pytest.raises(DuplicateRunError):
            store.start_run(SLOT, SLOT)
    stored = datetime.fromisoformat(read(database, "SELECT slot FROM runs")[0]["slot"])
    assert stored == SLOT
    assert stored.utcoffset() == timedelta(0)


@pytest.mark.parametrize("slot,started", [
    (SLOT.replace(tzinfo=None), SLOT),
    (SLOT, SLOT.replace(tzinfo=None)),
    (SLOT + timedelta(seconds=1), SLOT + timedelta(seconds=1)),
    (SLOT, SLOT - timedelta(seconds=1)),
])
def test_invalid_start_times(database, slot, started):
    with Storage(database) as store:
        with pytest.raises(ValueError):
            store.start_run(slot, started)
    assert read(database, "SELECT * FROM runs") == []


@pytest.mark.parametrize("results,finished", [
    ([], FINISHED),
    ([CheckResult("https://example.com/", ())], FINISHED),
    ([result()], SLOT - timedelta(seconds=1)),
    ([result()], FINISHED.replace(tzinfo=None)),
])
def test_invalid_completion_preserves_running(database, results, finished):
    with Storage(database) as store:
        run_id = store.start_run(SLOT, SLOT)
        with pytest.raises(ValueError):
            store.finish_run(run_id, results, finished)
    assert read(database, "SELECT status FROM runs")[0]["status"] == "running"
    assert read(database, "SELECT * FROM checks") == []


def test_open_error_is_sanitized(tmp_path):
    parent = tmp_path / "test-sensitive-location"
    parent.write_text("not a directory")
    with pytest.raises(StorageError, match="初始化") as caught:
        Storage(parent / "healthcheck.sqlite3")
    assert "test-sensitive-location" not in str(caught.value)


def test_target_is_bound_as_data_not_sql(database):
    target = "https://example.com/?q='; DROP TABLE runs;--"
    with Storage(database) as store:
        run_id = store.start_run(SLOT, SLOT)
        store.finish_run(run_id, [result(target)], FINISHED)
    assert read(database, "SELECT target FROM checks")[0]["target"] == target
    assert len(read(database, "SELECT * FROM runs")) == 1


def complete(store, minute=0, failures=((),)):
    """保存一次已完成运行；这里构造结果，不真的请求网页。"""
    slot = SLOT + timedelta(minutes=minute)
    run_id = store.start_run(slot, slot)
    store.finish_run(run_id, [result(failures=failures, started_at=slot)], slot + timedelta(seconds=30))
    return run_id


def test_first_reconciliation_sets_current_baseline_only(database):
    now = SLOT + timedelta(minutes=3)
    with Storage(database) as store:
        assert store.reconcile_schedule(now, ScheduleSettings()) == []
    state = read(database, "SELECT * FROM schedule_state")[0]
    assert datetime.fromisoformat(state["baseline_slot"]) == SLOT
    assert datetime.fromisoformat(state["next_slot"]) == SLOT
    assert read(database, "SELECT * FROM events") == []
    assert read(database, "SELECT * FROM runs") == []


def test_normal_schedule_has_no_missing_events(database):
    with Storage(database) as store:
        for minute in (0, 5, 10):
            assert store.reconcile_schedule(SLOT + timedelta(minutes=minute), ScheduleSettings()) == []
            complete(store, minute)
        assert store.reconcile_schedule(SLOT + timedelta(minutes=15), ScheduleSettings()) == []
    assert read(database, "SELECT * FROM events") == []


def test_continuous_downtime_is_one_range_and_is_not_replayed(database):
    with Storage(database) as store:
        store.reconcile_schedule(SLOT, ScheduleSettings())
        complete(store)
    # 模拟进程停止后再启动，检测进度必须来自磁盘，不能依赖实例内存。
    with Storage(database) as store:
        events = store.reconcile_schedule(SLOT + timedelta(days=30), ScheduleSettings())
        assert len(events) == 1
        assert events[0].kind == "missed"
        assert events[0].range_start == SLOT + timedelta(minutes=5)
        assert events[0].range_end == SLOT + timedelta(days=30, minutes=-5)
        assert store.reconcile_schedule(SLOT + timedelta(days=30), ScheduleSettings()) == []
    # 只生成一个区间事件，没有为历史缺口新增运行或补做检查。
    assert len(read(database, "SELECT * FROM runs")) == 1
    assert len(read(database, "SELECT * FROM checks")) == 1
    assert len(read(database, "SELECT * FROM events")) == 1


def test_existing_runs_split_missing_ranges(database):
    with Storage(database) as store:
        store.reconcile_schedule(SLOT, ScheduleSettings())
        complete(store, 0)
        complete(store, 10)
        events = store.reconcile_schedule(SLOT + timedelta(minutes=20), ScheduleSettings())
    assert [(event.range_start, event.range_end) for event in events] == [
        (SLOT + timedelta(minutes=5), SLOT + timedelta(minutes=5)),
        (SLOT + timedelta(minutes=15), SLOT + timedelta(minutes=15)),
    ]


@pytest.mark.parametrize("seconds", [59, 60, 61])
def test_current_late_start_is_not_counted_as_missing(database, seconds):
    """宽限前、恰好到期、刚超过宽限：当前轮都不能被自己误判为漏跑。"""
    with Storage(database) as store:
        store.reconcile_schedule(SLOT, ScheduleSettings())
        complete(store)
        now = SLOT + timedelta(minutes=5, seconds=seconds)
        assert store.reconcile_schedule(now, ScheduleSettings()) == []
        run_id = store.start_run(SLOT + timedelta(minutes=5), now)
        assert run_id > 0
    assert read(database, "SELECT * FROM events") == []


@pytest.mark.parametrize("interval,grace", [(1, 0.0), (1, 59.0), (60, 60.0)])
def test_schedule_boundary_intervals(database, interval, grace):
    settings = ScheduleSettings(interval_minutes=interval, grace_seconds=grace)
    with Storage(database) as store:
        store.reconcile_schedule(SLOT, settings)
        complete(store)
        now = SLOT + timedelta(minutes=interval * 2)
        events = store.reconcile_schedule(now, settings)
    assert len(events) == 1
    assert events[0].range_start == events[0].range_end == SLOT + timedelta(minutes=interval)


def test_interrupted_run_is_detected_once_after_restart(database):
    with Storage(database) as store:
        store.reconcile_schedule(SLOT, ScheduleSettings())
        run_id = store.start_run(SLOT, SLOT)
    with Storage(database) as store:
        now = SLOT + timedelta(minutes=15)
        events = store.reconcile_schedule(now, ScheduleSettings())
        assert [event.kind for event in events] == ["interrupted", "missed"]
        assert events[0].run_id == run_id
        assert events[1].range_start == SLOT + timedelta(minutes=5)
        assert events[1].range_end == SLOT + timedelta(minutes=10)
        assert store.reconcile_schedule(now, ScheduleSettings()) == []
    assert read(database, "SELECT status FROM runs")[0]["status"] == "interrupted"


def test_long_completed_run_leaves_skipped_slots_as_missing(database):
    with Storage(database) as store:
        store.reconcile_schedule(SLOT, ScheduleSettings())
        run_id = store.start_run(SLOT, SLOT)
        store.finish_run(run_id, [result()], SLOT + timedelta(minutes=16))
        events = store.reconcile_schedule(SLOT + timedelta(minutes=20), ScheduleSettings())
    assert len(events) == 1
    assert events[0].kind == "missed"  # 执行久不代表 interrupted。
    assert events[0].range_start == SLOT + timedelta(minutes=5)
    assert events[0].range_end == SLOT + timedelta(minutes=15)


def test_period_change_resets_baseline_without_retrospective_gaps(database):
    with Storage(database) as store:
        store.reconcile_schedule(SLOT, ScheduleSettings())
        complete(store)
        now = SLOT + timedelta(minutes=32)
        changed = ScheduleSettings(interval_minutes=10)
        events = store.reconcile_schedule(now, changed)
        assert [event.kind for event in events] == ["schedule_changed"]
        assert store.reconcile_schedule(now, changed) == []
        # 周期变更本身不产生故障邮件。
        assert store.pending_report(now, 5) is None
        events = store.reconcile_schedule(SLOT + timedelta(minutes=40), changed)
        assert events[0].range_start == events[0].range_end == SLOT + timedelta(minutes=30)
    assert read(database, "SELECT interval_minutes FROM schedule_state")[0]["interval_minutes"] == 10


def test_reconciliation_does_not_rewind_persisted_cursor(database):
    with Storage(database) as store:
        store.reconcile_schedule(SLOT, ScheduleSettings())
        complete(store)
        store.reconcile_schedule(SLOT + timedelta(minutes=15), ScheduleSettings())
        assert store.reconcile_schedule(SLOT + timedelta(minutes=10), ScheduleSettings()) == []
    assert datetime.fromisoformat(read(database, "SELECT next_slot FROM schedule_state")[0][0]) == SLOT + timedelta(minutes=15)


def test_reconciliation_rolls_back_interruption_and_cursor_on_error(database):
    with Storage(database) as store:
        store.reconcile_schedule(SLOT, ScheduleSettings())
        store.start_run(SLOT, SLOT)
        # 先允许中断事件写入，再在漏跑事件处失败，验证整次恢复事务一起回滚。
        with store._connection:
            store._connection.execute("""CREATE TRIGGER reject_missed BEFORE INSERT ON events
                WHEN NEW.kind = 'missed' BEGIN SELECT RAISE(ABORT, 'test failure'); END""")
        with pytest.raises(StorageError, match="核对"):
            store.reconcile_schedule(SLOT + timedelta(minutes=15), ScheduleSettings())
    assert read(database, "SELECT status FROM runs")[0][0] == "running"
    assert datetime.fromisoformat(read(database, "SELECT next_slot FROM schedule_state")[0][0]) == SLOT
    assert read(database, "SELECT * FROM events") == []


def test_empty_report_and_running_report_are_not_sent(database):
    with Storage(database) as store:
        assert store.pending_report(FINISHED, 5) is None
        complete(store, failures=(("keyword",),))
        store.start_run(SLOT + timedelta(minutes=5), SLOT + timedelta(minutes=5))
        assert store.pending_report(SLOT + timedelta(minutes=6), 5) is None


def test_report_contains_old_failure_and_latest_recovery(database):
    with Storage(database) as store:
        failed_run = complete(store, failures=(("keyword",),))
        complete(store, 5)
        report = store.pending_report(SLOT + timedelta(minutes=6), 5)
    assert len(report.events) == 1
    assert report.events[0].run_id == failed_run
    assert not report.failed_checks[0].result.success
    assert report.latest_checks[0].result.success
    assert report.failed_checks[0].result.attempts[0].failure_codes == ("keyword",)
    assert report.period_start == FINISHED
    assert report.event_ids == (report.events[0].id,)


def test_report_contains_missing_and_interrupted_details(database):
    with Storage(database) as store:
        store.reconcile_schedule(SLOT, ScheduleSettings())
        store.start_run(SLOT, SLOT)
        now = SLOT + timedelta(minutes=15)
        store.reconcile_schedule(now, ScheduleSettings())
        report = store.pending_report(now, 5)
    assert {event.kind for event in report.events} == {"missed", "interrupted"}
    assert report.period_start == SLOT + timedelta(minutes=5)
    assert report.failed_checks == report.latest_checks == ()


@pytest.mark.parametrize("outcome", ["success", "failed"])
def test_delivery_interval_advances_after_success_or_failure(database, outcome):
    with Storage(database) as store:
        complete(store, failures=(("keyword",),))
        report = store.pending_report(FINISHED, 5)
        store.record_delivery(report, outcome, FINISHED)
        complete(store, 5, failures=(("keyword",),))
        # 故意传 7 分钟邮件间隔，证明它与 5 分钟检查周期独立。
        assert store.pending_report(FINISHED + timedelta(minutes=7, microseconds=-1), 7) is None
        next_report = store.pending_report(FINISHED + timedelta(minutes=7), 7)
        assert len(next_report.events) == (1 if outcome == "success" else 2)
    delivery = read(database, "SELECT * FROM deliveries")[0]
    assert delivery["outcome"] == outcome
    assert delivery["error_code"] == (None if outcome == "success" else "smtp_error")


def test_success_only_marks_events_in_report_snapshot(database):
    with Storage(database) as store:
        complete(store, failures=(("keyword",),))
        report = store.pending_report(FINISHED, 5)
        complete(store, 5, failures=(("status",),))
        store.record_delivery(report, "success", SLOT + timedelta(minutes=6))
        pending = store.pending_report(SLOT + timedelta(minutes=11), 5)
    events = read(database, "SELECT * FROM events ORDER BY id")
    assert events[0]["notified_at"] is not None
    assert events[1]["notified_at"] is None
    assert pending.event_ids == (events[1]["id"],)
    assert read(database, "SELECT event_id FROM delivery_events")[0][0] == events[0]["id"]


def test_failed_delivery_preserves_events_across_restart(database):
    with Storage(database) as store:
        complete(store, failures=(("keyword",),))
        report = store.pending_report(FINISHED, 5)
        store.record_delivery(report, "failed", FINISHED)
    with Storage(database) as store:
        retried = store.pending_report(FINISHED + timedelta(minutes=5), 5)
        assert retried.event_ids == report.event_ids
        store.record_delivery(retried, "success", FINISHED + timedelta(minutes=5))
    assert read(database, "SELECT notified_at FROM events")[0][0] is not None
    assert len(read(database, "SELECT * FROM deliveries")) == 2


def test_delivery_mark_and_history_roll_back_together(database):
    with Storage(database) as store:
        complete(store, failures=(("keyword",),))
        report = store.pending_report(FINISHED, 5)
        with store._connection:
            store._connection.execute("""CREATE TRIGGER reject_notification BEFORE UPDATE OF notified_at ON events
                BEGIN SELECT RAISE(ABORT, 'test failure'); END""")
        with pytest.raises(StorageError, match="通知结果"):
            store.record_delivery(report, "success", FINISHED)
    assert read(database, "SELECT * FROM deliveries") == []
    assert read(database, "SELECT * FROM delivery_events") == []
    assert read(database, "SELECT notified_at FROM events")[0][0] is None


def test_success_cannot_be_recorded_twice(database):
    with Storage(database) as store:
        complete(store, failures=(("keyword",),))
        report = store.pending_report(FINISHED, 5)
        store.record_delivery(report, "success", FINISHED)
        with pytest.raises(StorageError, match="已经通知"):
            store.record_delivery(report, "success", FINISHED)
    assert len(read(database, "SELECT * FROM deliveries")) == 1


def test_report_query_does_not_mark_or_hold_read_lock(database):
    with Storage(database) as store:
        complete(store, failures=(("keyword",),))
        report = store.pending_report(FINISHED, 5)
        assert store.pending_report(FINISHED, 5).event_ids == report.event_ids
        # 查询返回后另一连接可写入提交，证明 SMTP 等待期间不持有旧读锁。
        with closing(sqlite3.connect(database, timeout=0)) as connection:
            connection.execute("INSERT INTO deliveries (attempted_at, outcome) VALUES (?, 'failed')", (FINISHED.isoformat(),))
            connection.commit()
    assert read(database, "SELECT notified_at FROM events")[0][0] is None


def test_invalid_notification_arguments_do_not_write(database):
    with Storage(database) as store:
        complete(store, failures=(("keyword",),))
        report = store.pending_report(FINISHED, 5)
        with pytest.raises(ValueError):
            store.pending_report(FINISHED, 0)
        with pytest.raises(ValueError):
            store.record_delivery(report, "test-invalid-outcome", FINISHED)
        with pytest.raises(ValueError):
            store.record_delivery(report, "success", SLOT)
    assert read(database, "SELECT * FROM deliveries") == []


@pytest.mark.parametrize("checks_saved", [False, True])
def test_stopped_execution_is_recorded_once(database, checks_saved):
    """新实例终止旧实例后记中断；若网页结果已保存，不丢明细和原检查结束时间。"""
    with Storage(database) as store:
        run_id = store.start_run(SLOT, SLOT)
        if checks_saved:
            store.finish_run(run_id, [result()], FINISHED)
        now = SLOT + timedelta(minutes=5)
        events = store.reconcile_schedule(now, ScheduleSettings(), interrupted_run_id=run_id)
        assert [event.kind for event in events] == ["interrupted"]
        run = read(database, "SELECT status, finished_at FROM runs")[0]
        assert run["status"] == "interrupted"
        assert datetime.fromisoformat(run["finished_at"]) == (FINISHED if checks_saved else now)
        assert len(read(database, "SELECT * FROM checks")) == int(checks_saved)
        report = store.pending_report(now, 5)
        assert len(report.latest_checks) == int(checks_saved)
        # 再次核对同一编号不能重复生成事件，也不能把保留下来的检查结果清掉。
        assert store.reconcile_schedule(now, ScheduleSettings(), interrupted_run_id=run_id) == []
