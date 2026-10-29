# 安装、运行与维护

完整功能、模块和运行逻辑见 [中文 README](../README_CN.md) / [English README](../README.md)；本文补充具体部署操作和维护细节。

## 准备环境与配置

初版部署在单台 Linux，Mac 用于开发。需要 Python 3.12、uv、可写的本机磁盘，以及访问目标网站和 Gmail SMTP 的网络；不用消息队列或数据库服务器。SQLite 随 Python 提供，不需要额外启动服务。

在仓库根目录执行 `uv sync --locked`，按锁文件安装依赖及已注册的 `website-healthcheck` 命令。后续每次更新代码和依赖，先暂停本项目的 cron，确认旧实例已经退出，再同步环境、运行测试并恢复调度。不要在旧实例工作期间替换它的运行环境。

真实配置保存在 `src/website_healthcheck/config/config.yaml`。若它不存在才执行下面的复制；已有文件保留原样：

```sh
test -e src/website_healthcheck/config/config.yaml || cp src/website_healthcheck/config/config.yaml.example src/website_healthcheck/config/config.yaml
chmod 600 src/website_healthcheck/config/config.yaml
mkdir -p src/website_healthcheck/config/var
chmod 700 src/website_healthcheck/config/var
```

编辑真实配置中的 `mail.username`、`password`、`sender` 和 `recipients`。Gmail 使用 `smtp.gmail.com:465` 和应用专用密码；账户是否允许创建此密码取决于两步验证及组织策略，参考 [Google 官方说明](https://support.google.com/accounts/answer/185833)。示例占位值能通过字段校验，但不能用于真实认证。不要在终端输出、Issue 或截图里粘贴密码。

配置、各字段默认值与校验规则见 [配置说明](configuration.md) 和 [设计表](planning/implementation-plan.md)。相对数据库及锁路径以配置文件目录为基准；示例生成的数据在 `config/var/`，与当前终端目录无关。同一数据库必须共用同一锁路径，不要放在跨机器共享磁盘上。

真实 `config.yaml` 已被 Git 忽略，只有无凭证示例会提交。`config/var/` 只跟踪 `.gitkeep`；如果自定义到其他运行目录，应另行确认该目录不会被 Git 跟踪。wheel 安装不包含真实配置，建议通过 `--config` 明确指定外部可写配置。

## 手动执行与退出码

在仓库根目录执行：

```sh
uv run --locked website-healthcheck --help
uv run --locked website-healthcheck --config "$PWD/src/website_healthcheck/config/config.yaml"
echo "退出码：$?"
```

第二条命令会真正请求网站、写库，并按待通知事件决定是否发信。入口是安装时注册的命令，不是 `uv run main.py`。一次启动只执行当前轮，执行结束就退出，不会在后台等待五分钟。

| 退出码 | 含义与处理 |
| --- | --- |
| 0 | 本轮目标最终健康，且无执行错误；历史漏跑和故障仍可能被汇总 |
| 1 | 至少一个目标在重试后仍失败；检查数据库或汇总邮件 |
| 2 | 配置、数据库、SMTP 或终止旧实例等执行错误；查看标准错误/cron 日志 |
| 3 | 同一时段重复启动，另一实例正在处理接替，或触发自身/父辈保护；不会重复检查 |

五分钟周期内第二次手动运行可能返回 3，这不代表网站故障。执行错误优先于网站失败；例如本轮网站失败且 SMTP 失败，最终返回 2。不要通过删除数据库或锁文件强行绕过重复保护。

## Linux cron 部署

完整启动器、日志权限、cron 生成与安装示例见 [命令与调度说明](scheduling.md#生成与手动安装-cron)。这里列出安装次序：

1. 完成配置，先手动运行启动器，确认绝对路径、权限及运行环境正确。
2. 预先创建日志父目录和文件，目录设 0700、文件设 0600。
3. 用 `command -v uv` 取得 uv 的绝对路径，执行 `scripts/print_cron.py` 生成片段。
4. 用 `crontab -l` 查看已有条目，`crontab -e` 追加本项目片段，保留其他任务。
5. 下一个计划时刻后查看日志和 SQLite，确认实际启动；修改周期后重新生成并更新 cron。

默认 `*/5 * * * *` 在每小时的 00、05、10 等分钟启动，不是安装后等待五分钟。检查周期与邮件最小间隔分别配置，默认均为五分钟；邮件只在本轮检查结束后发送，首次事件可以立即发，无事件不发空邮件。

Cronie 可用生成片段的 `CRON_TZ=UTC`；不支持它的 Linux cron 需在守护进程层面保证 UTC 调度。脚本中的 `TZ=UTC` 只控制子进程，不会改变 cron 触发时间。片段的时区变量可能影响后续其他任务，安装时应保留它们的时区安排。停止本项目时只删除其 cron 条目，不运行会删掉所有条目的 `crontab -r`。

日志追加 stdout/stderr，初版没有内置轮转。请在 Linux 上为所选日志路径配置 logrotate，并设保留天数；历史数据库也不自动清理。具体保留周期在部署前决定，不在初版程序里默默删除记录。

## 测试结束后停用 cron

执行 `crontab -l` 查看任务，再用 `EDITOR=nano crontab -e` 只删除调用本项目 `scripts/run_healthcheck.sh` 的任务行。Ctrl+O、回车保存，Ctrl+X 退出；重新执行 `crontab -l` 确认。不要运行 `crontab -r`，它会删除所有用户任务。

若添加了 `CRON_TZ=UTC`，先确认其他条目不依赖它再删除；该变量可能影响后续任务。删除条目不终止已经启动的进程，只阻止下一轮启动，也不删除配置、数据库或日志。维护前仍须确认现有实例已结束。

## Mac 的可选真实验证

用户于 2026-10-18 确认已完成 Mac 本地真实测试且无问题。以下保留操作说明；该反馈不代表已验证 Linux 生产主机。

Mac 上可先从终端执行同一个 `/bin/sh scripts/run_healthcheck.sh /absolute/uv /absolute/config.yaml`。若以后安装 cron，用 `crontab -e` 保留原条目并追加任务；不要假定 Mac 支持 `CRON_TZ`。本项目周期都是 60 的因数，在当前 JST（UTC+9、无夏令时）下，分钟刻度与 UTC 对齐；其他时区需另行核对。

项目位于 Desktop，后台 cron 可能受 macOS 文件隐私限制。建议先把诊断日志放到 `$HOME/Library/Logs/website-healthcheck/cron.log`，事先创建目录并限制权限。如果出现 `Operation not permitted`，再检查系统隐私设置中的完全磁盘访问权限和 `/usr/sbin/cron`；不要仅因终端能运行就认为后台也具备权限。参考 [Apple 文件访问控制](https://support.apple.com/guide/security/controlling-app-access-to-files-secddd1d86a6/web)。

保持网络与机器唤醒，必要时在另一终端运行 `caffeinate -i -t 1200`，阻止二十分钟内因空闲自动睡眠。合盖或其他原因仍可能使机器休眠；cron 不会在睡眠期间检查或发信。参考 [Apple 定时任务说明](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/ScheduledJobs.html)。

## 查看记录、备份与故障排查

示例路径下可用以下只读 SQL 查看最近几轮和待通知事件；若使用自定义数据库路径，应替换命令中的路径。需要系统提供 `sqlite3` 命令行工具，程序自身不依赖此工具：

```sh
sqlite3 -readonly -header -column src/website_healthcheck/config/var/healthcheck.sqlite3 \
  'SELECT id, slot, started_at, finished_at, status FROM runs ORDER BY id DESC LIMIT 10;'
sqlite3 -readonly -header -column src/website_healthcheck/config/var/healthcheck.sqlite3 \
  "SELECT id, kind, range_start, range_end, notified_at FROM events WHERE notified_at IS NULL AND kind IN ('check_failed', 'missed', 'interrupted') ORDER BY id;"
sqlite3 -readonly -header -column src/website_healthcheck/config/var/healthcheck.sqlite3 \
  'SELECT id, attempted_at, outcome, error_code FROM deliveries ORDER BY id DESC LIMIT 10;'
```

时间统一记录为 UTC。`running` 表示开始记录已保存，不能单凭它认定进程还活着；`completed` 表示检查已保存，网站可能仍有故障，后续邮件也可能失败；`interrupted` 表示确认旧实例退出后发现它没完成。表和字段用途见 [SQLite 说明](storage.md)。

若数据库尚不存在，上面的只读命令会失败，不会意外创建一个空库。检查失败时也会保存各次尝试；不需要去日志中寻找完整网页或密码。

备份前暂停本项目 cron，确认现有实例已退出，再复制数据库到单独的备份目录，并保护配置凭证。恢复备份时同样先停止运行，不要一边检查一边直接覆盖数据库。恢复后的下一轮会识别停机缺口，只记漏跑事件，不执行历史 HTTP。初版无自动表迁移或历史清理工具。

常见情况：

- 没收到邮件：先查是否有最终故障、漏跑或中断事件，以及是否到了邮件最小间隔；重试后恢复不会产生最终故障告警。再看 `deliveries` 和执行错误。SMTP 接受并不等于最终进入收件箱。
- `running` 长期保留：检查 cron 是否有新启动、配置和锁路径是否正确；后续新时段会核对并终止旧实例，确认拿到锁后才把旧轮记中断。无法确认身份时返回 2，应检查实际进程，不能直接删除锁绕过。
- 机器停机没有即时告警：本机程序不运行就无法发信，只有恢复后才能发现漏跑，需要生产环境的独立心跳监控解决。
- 重复收到邮件：SMTP 已接受、数据库标记前退出，或部分收件人拒收，均可能在重试时重复通知；初版不承诺严格一次投递。

## 生产前补充

独立心跳服务发现整机或 cron 停摆；通过凭证管理替代明文配置；定义告警抑制、升级和收件人策略；落实日志轮转、数据库保留及备份恢复演练；验证实际网站、Gmail、系统 cron 和目标机器权限。专业调度平台、多机协调和数据库迁移需要时另立任务，初版不实现。

发布流程见 [GitHub Release 说明](releases.md)：初版验收、PR 合并且用户明确授权后才推送 `v0.1.0`，由 GitHub Actions 校验并在测试通过后创建 Release。
