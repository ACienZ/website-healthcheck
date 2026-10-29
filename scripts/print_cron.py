"""读取配置并输出用户 crontab 片段；只打印，不安装或覆盖现有任务。

用 uv run --locked python scripts/print_cron.py --help 查看参数。
这个辅助脚本不检查网页、不写数据库、不连接邮件服务器。
"""

import argparse
import os
from pathlib import Path
import shlex
import sys

from website_healthcheck.config.load_config import ConfigError, load_config
from website_healthcheck.main import DEFAULT_CONFIG

LAUNCHER = Path(__file__).resolve().with_name("run_healthcheck.sh")


def cron_text(interval: int, uv: Path, config: Path, log: Path) -> str:
    """根据已校验的配置周期生成 UTC cron 行，正确引用含空格或引号的路径。

    cron 会先解析百分号，再交给 shell，因此单纯 shlex.quote 不能保护 %。
    初版明确拒绝百分号和换行路径，不输出可能拆成多条任务的片段。
    """
    paths = [LAUNCHER, uv, config, log]
    if any(any(char in str(path) for char in ("%", "\n", "\r", "\x00")) for path in paths):
        raise ValueError("cron 路径不能包含百分号、换行或空字符")
    # 配置已限定 interval 为 60 的正因数；每小时用第 0 分钟，不能写 */60。
    minute = "0" if interval == 60 else f"*/{interval}"
    command = shlex.join(["/bin/sh", str(LAUNCHER), str(uv), str(config)])
    # 重定向放在 cron 行上，也能收集启动器自己的错误；目录须在安装时创建。
    return f"CRON_TZ=UTC\n{minute} * * * * {command} >> {shlex.quote(str(log))} 2>&1\n"


def main(argv: list[str] | None = None) -> int:
    """校验配置及路径并打印片段，失败返回 2；不调用 crontab。"""
    parser = argparse.ArgumentParser(description="输出 UTC cron 片段，由用户手动安装")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--uv", type=Path, required=True, help="安装时确认的 uv 可执行文件路径")
    parser.add_argument("--log", type=Path, required=True, help="日志文件路径，父目录须预先创建")
    args = parser.parse_args(argv)
    try:
        config_path, uv_path, log_path = args.config.resolve(), args.uv.resolve(), args.log.resolve()
        config = load_config(config_path)
        if not uv_path.is_file() or not os.access(uv_path, os.X_OK):
            raise ValueError("uv 不存在或不可执行")
        # 防止将运行输出意外追加到配置、数据库或锁文件上。
        lock = config.storage.lock_path
        lock_files = {lock, *(lock.with_name(lock.name + suffix) for suffix in (
            ".startup", ".owner.json", ".owner.json.tmp",
        ))}
        if log_path in {config_path, config.storage.database_path, *lock_files}:
            raise ValueError("日志不能与配置、数据库、锁或进程信息文件使用同一路径")
        text = cron_text(config.schedule.interval_minutes, uv_path, config_path, log_path)
    except (ConfigError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except OSError:
        print("无法解析 cron 文件路径", file=sys.stderr)
        return 2
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
