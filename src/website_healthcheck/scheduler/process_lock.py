"""新一轮开始时，先结束仍未退出的旧实例，再独占执行当前这一轮。

flock 保证不会同时执行两轮；旁边的 JSON 只记录谁持锁及对应运行编号。
psutil 根据 PID 和进程创建时间核对身份，避免 PID 被重新使用后误终止其他进程。
阅读顺序：process_lock → _stop_previous → _wait_for_lock → RunLock。
"""

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
import errno
import fcntl
import json
import math
import os
from pathlib import Path
from time import monotonic, sleep
from typing import Iterator, TextIO

import psutil


class LockError(RuntimeError):
    """无法确认旧实例已停止，或无法准备锁；调用方应报错，不能并行启动。"""


class LockBusyError(LockError):
    """重复启动或另一个新实例正在处理旧实例；当前实例退出，不参与争抢。"""


def _try_lock(stream: TextIO) -> bool:
    """尝试拿锁：拿到返回 True，已有实例持有返回 False，其他系统错误继续抛出。"""
    try:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError as exc:
        if exc.errno in (errno.EACCES, errno.EAGAIN):
            return False
        raise


def _wait_for_lock(stream: TextIO, seconds: float) -> bool:
    """最多等指定秒数，直到旧实例释放锁；用实际拿到锁确认可以开始新一轮。"""
    deadline = monotonic() + seconds
    while not _try_lock(stream):
        remaining = deadline - monotonic()
        if remaining <= 0:
            return False
        sleep(min(0.05, remaining))
    return True


def _read_owner(path: Path) -> dict:
    """读取锁持有者信息；字段不完整时不猜 PID，也不发送终止信号。"""
    try:
        owner = json.loads(path.read_text(encoding="utf-8"))
        if (
            not isinstance(owner, dict)
            or not {"pid", "created_at", "finished", "slot", "run_id"} <= owner.keys()
            or type(owner.get("pid")) is not int or owner["pid"] <= 1
            or type(owner.get("created_at")) not in (int, float)
            or not math.isfinite(owner["created_at"]) or owner["created_at"] <= 0
            or type(owner.get("finished")) is not bool
            or (owner.get("slot") is not None and not isinstance(owner["slot"], str))
            or (owner.get("run_id") is not None and (
                type(owner["run_id"]) is not int or owner["run_id"] <= 0
            ))
        ):
            raise ValueError
        return owner
    except (OSError, ValueError, TypeError):
        raise LockError("无法确认旧实例身份，未终止任何进程") from None


def _write_owner(path: Path, owner: dict) -> None:
    """先写临时文件再替换 JSON，避免其他实例读到只写了一半的 PID 信息。"""
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(owner), encoding="utf-8")
    temporary.replace(path)


@dataclass
class RunLock:
    """把进程与数据库运行编号关联，供下一实例记录“哪一轮被终止了”。

    interrupted_run_id 是本次接替的旧运行编号；没有接替旧实例时为 None。
    set_run_id 在 start_run 提交后调用，HTTP 和 SMTP 阶段都保留这个关联。
    """

    _owner_path: Path
    _owner: dict
    interrupted_run_id: int | None = None

    def set_run_id(self, run_id: int, slot: datetime) -> None:
        """开始记录已落库，再登记编号和实际时段；等待终止期间可能已跨时段。"""
        self._owner["run_id"] = run_id
        self._owner["slot"] = slot.isoformat()
        try:
            _write_owner(self._owner_path, self._owner)
        except OSError:
            raise LockError("无法记录本轮进程信息") from None


def _stop_previous(stream: TextIO, path: Path, owner_path: Path, slot: str | None, timeout: float) -> int | None:
    """确认旧实例身份，请求退出；超时则强制结束，拿到锁后返回其运行编号。

    不杀同一时段的重复启动、不杀当前进程或它的父辈进程，也不按程序名批量杀进程。
    即使旧进程不响应 SIGTERM，也会在有上限的等待后发送 SIGKILL。
    最后仍拿不到锁就报错，不打开数据库，不假装已经终止成功。
    """
    owner = _read_owner(owner_path)
    current = psutil.Process()
    if owner["pid"] in {current.pid, *(parent.pid for parent in current.parents())}:
        raise LockBusyError("已有任务运行；不能终止当前进程或它的父辈进程")
    if slot is not None and owner["slot"] == slot:
        raise LockBusyError("该时段已有任务运行，不重复启动")
    process = None
    try:
        process = psutil.Process(owner["pid"])
        if process.create_time() != owner["created_at"]:
            raise LockError("旧实例 PID 已被其他进程使用，未发送终止信号")
        # 还须确认它确实打开了本项目的锁文件，不只相信磁盘里残留的 PID。
        if not any(Path(file.path) == path for file in process.open_files()):
            raise LockError("进程与本项目的锁文件不匹配，未发送终止信号")
        process.terminate()
    except psutil.NoSuchProcess:
        pass  # 检查身份期间旧实例自行退出，接下来仍须实际拿到锁。
    if not _wait_for_lock(stream, timeout):
        if process is None:
            raise LockError("找不到旧实例，但锁仍未释放，本轮未执行")
        try:
            # psutil 在发送信号前还会检查 PID 是否已被重新使用。
            process.kill()
        except psutil.NoSuchProcess:
            pass
        if not _wait_for_lock(stream, timeout):
            raise LockError("旧实例未能停止，本轮未执行；请检查旧进程")

    # 旧实例可能在退出前刚写入 run_id，或恰好正常完成；停止后再读最后状态。
    last = _read_owner(owner_path)
    return None if last["finished"] else last["run_id"]


@contextmanager
def process_lock(path: Path, *, slot: datetime | None = None, stop_timeout_seconds: float = 5.0) -> Iterator[RunLock]:
    """保证当前轮独占执行；新时段启动时先停止仍未结束的旧实例。

    stop_timeout_seconds 是请求退出后的等待上限；强制终止后再等同样时长。
    旁边的 .startup 锁只在“确认旧实例停止、登记新实例”期间持有，避免 B、C
    同时启动时互相终止。真正的执行锁持有到 HTTP、SMTP 和数据库操作全部结束。
    锁文件不能删除：进程退出后内核自动释放锁，文件留在磁盘是正常的。
    """
    path = path.resolve()
    owner_path = path.with_name(path.name + ".owner.json")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        stream = path.open("a")
    except OSError:
        raise LockError("无法创建或打开进程锁") from None

    with stream:
        try:
            # 先让启动操作依次进行；竞争者立即退出，不无限等另一轮启动。
            with path.with_name(path.name + ".startup").open("a") as startup:
                if not _try_lock(startup):
                    raise LockBusyError("另一个新实例正在处理旧任务，本轮不重复启动")
                previous_id = None
                if not _try_lock(stream):
                    previous_id = _stop_previous(
                        stream, path, owner_path, slot.isoformat() if slot else None,
                        stop_timeout_seconds,
                    )
                owner = {
                    "pid": os.getpid(), "created_at": psutil.Process().create_time(),
                    "slot": slot.isoformat() if slot else None, "run_id": None, "finished": False,
                }
                _write_owner(owner_path, owner)
                lease = RunLock(owner_path, owner, previous_id)
        except (OSError, psutil.Error):
            raise LockError("无法确认或终止旧实例，本轮未执行") from None

        # 此处旧实例已停止，当前实例独占执行；with 内的业务异常原样交给主入口处理。
        yield lease
        # 只有整段正常结束才记 finished；若恰好被下一轮接替，不误报已正常结束。
        lease._owner["finished"] = True
        try:
            _write_owner(owner_path, lease._owner)
        except OSError:
            raise LockError("无法记录本轮结束状态") from None
