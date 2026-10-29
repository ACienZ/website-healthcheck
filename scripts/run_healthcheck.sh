#!/bin/sh
# cron 专用启动器：所有路径由安装时确定，不依赖交互式 shell 的 PATH 或 cwd。
# 调用：/bin/sh /绝对路径/run_healthcheck.sh /绝对路径/uv /绝对路径/config.yaml
set -eu
umask 077

if [ "$#" -ne 2 ]; then
    echo '用法：run_healthcheck.sh /absolute/uv /absolute/config.yaml' >&2
    exit 2
fi

# 参数必须是绝对路径；错误只给出说明，不回显配置路径或参数内容。
case "$1" in /*) ;; *) echo 'uv 必须使用绝对路径' >&2; exit 2 ;; esac
case "$2" in /*) ;; *) echo '配置必须使用绝对路径' >&2; exit 2 ;; esac
if [ ! -x "$1" ]; then
    echo 'uv 不存在或不可执行' >&2
    exit 2
fi

# 从脚本自身位置定位仓库；项目路径有空格、中文时也按一个参数处理。
healthcheck_repo=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd -- "$healthcheck_repo"
export TZ=UTC
export PYTHONIOENCODING=utf-8

# 部署时先 uv sync --locked；定时运行不联网同步依赖，避免重复创建环境。
# exec 用 uv 替换 shell，保留 Python 的 0/1/2/3 退出码及终止信号。
exec "$1" run --locked --no-sync website-healthcheck --config "$2"
