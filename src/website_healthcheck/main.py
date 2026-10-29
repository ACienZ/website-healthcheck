"""每次启动只执行当前这一轮：停止旧实例 → 核对旧记录 → 检查 → 保存 → 发邮件。

main 负责读配置和给出退出码；_run_once 说明各模块的调用顺序。
网站检查失败会保存结果；程序中途退出则在下一轮记下中断，不重新执行旧任务。
"""

import argparse
from datetime import UTC, datetime
from pathlib import Path
import sys

import httpx

from website_healthcheck.checker.checker import run_checks
from website_healthcheck.config.load_config import Config, ConfigError, load_config
from website_healthcheck.notifier.notifier import MailError, send_alert
from website_healthcheck.scheduler.process_lock import LockBusyError, LockError, process_lock
from website_healthcheck.storage.storage import DuplicateRunError, Storage, StorageError

# 使用安装包内的配置位置，不依赖从哪个目录启动；不把真实配置打进分发包。
DEFAULT_CONFIG = Path(__file__).resolve().parent / "config" / "config.yaml"


def _run_once(config: Config) -> int:
    """执行当前时段的新任务，不继续或重新执行之前中断、漏跑的任务。

    1. 确定当前属于哪个时段；若旧时段实例还没退出，先终止并确认它已停止。
    2. 查数据库，给之前没跑完的任务补上中断标记，记下没启动过的历史时段。
    3. 新建本轮 running 记录，检查全部网站，保存结果后再处理汇总邮件。
    4. 关闭数据库并释放锁。网站结果决定退出码 0/1，执行错误交给 main 返回 2。
    """
    requested_at = datetime.now(UTC)
    interval = config.schedule.interval_minutes
    # 例如每 5 分钟一次，12:03 启动属于 12:00 时段；同一时段不重复执行。
    slot = requested_at.replace(
        minute=requested_at.minute // interval * interval, second=0, microsecond=0,
    )
    with process_lock(
        config.storage.lock_path, slot=slot,
        stop_timeout_seconds=config.schedule.stop_timeout_seconds,
    ) as execution:
        # 此时旧实例已经停止，当前实例独占执行。先确认这一点，才敢把数据库
        # 中旧的 running 改成“未完成执行”，否则可能把仍在工作的实例误判为中断。
        with Storage(config.storage.database_path) as store:
            started_at = datetime.now(UTC)
            # 停止旧实例要等待，可能跨过时段边界；按实际开始时刻重新计算，
            # 避免把已经过去的时段既记为漏跑、又当作本轮执行。
            slot = started_at.replace(
                minute=started_at.minute // interval * interval, second=0, microsecond=0,
            )
            store.reconcile_schedule(
                started_at, config.schedule, interrupted_run_id=execution.interrupted_run_id,
            )
            # 先把本轮开始保存到磁盘，再发请求。中途退出时就有记录可供下一轮判断。
            run_id = store.start_run(slot, started_at)
            execution.set_run_id(run_id, slot)

            with httpx.Client() as client:
                results = run_checks(config, client)
            # 检查结果、各次尝试、最终失败事件一起保存，随后把本轮标为 completed。
            # completed 只说明检查结果已保存；后面的邮件阶段仍可能失败或被终止。
            store.finish_run(run_id, results, datetime.now(UTC))

            # 当前结果保存完再汇总：既能报告过去的故障，也能附上刚检查出的最新状态。
            # 无待通知事件或邮件间隔未到，就不发邮件，也不在进程里等下一次发送时间。
            report = store.pending_report(datetime.now(UTC), config.mail.interval_minutes)
            if report is not None:
                attempted_at = datetime.now(UTC)
                try:
                    send_alert(report, config.mail)
                except MailError:
                    # 发信失败也记下尝试时间，避免每次启动都立即重发；事件仍未通知。
                    store.record_delivery(report, "failed", attempted_at)
                    raise
                # SMTP 接收后才标记本批已通知；两步之间退出，之后仍可能重复发送。
                store.record_delivery(report, "success", attempted_at)

            failed = sum(not result.success for result in results)
            print(f"检查完成：{len(results)} 个目标，{failed} 个最终失败。")
            # return 也会退出两个 with：先关闭数据库，再记下整轮结束并释放进程锁。
            return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    """读取配置并执行一轮：0 网站健康、1 网站故障、2 执行错误、3 重复启动。"""
    parser = argparse.ArgumentParser(description="执行一轮网站健康检查并汇总告警")
    parser.add_argument(
        "--config", type=Path, default=DEFAULT_CONFIG,
        help="配置文件路径；默认使用包内 config/config.yaml",
    )
    # 测试可传参数列表；平时运行时 argv=None，argparse 自动读取命令行。
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        return _run_once(config)
    except (LockBusyError, DuplicateRunError) as exc:
        # 同时重复启动、同一时段重复执行或试图终止自身/父进程时，返回 3。
        print(str(exc), file=sys.stderr)
        return 3
    except (ConfigError, LockError, StorageError, MailError) as exc:
        # 无法终止旧实例也属于执行错误；绝不能绕过锁让新旧两轮同时执行。
        # 这些异常文本已脱敏，不输出配置、密码或底层异常链。
        print(str(exc), file=sys.stderr)
        return 2
    except Exception:
        # 未预期异常也可能带凭证，只打印固定说明。检查未完成时保留 running，
        # 下次启动确认旧实例已退出后，再给它补上中断记录。
        print("任务执行失败；未完成的运行将在下次启动时识别为中断。", file=sys.stderr)
        return 2
