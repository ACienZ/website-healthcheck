# 命令入口与 cron 调度

## 阅读顺序与执行流程

先读 `src/website_healthcheck/main.py` 的 `main`，再读 `_run_once`，最后读 `scheduler/process_lock.py`。前者把各模块串起来，后者只负责互斥，不重写页面检查、SQL 或 SMTP。代码和测试包含中文职责说明与关键边界注释。

1. 读取配置，计算当前属于哪个 UTC 时段，例如每五分钟一次时，12:03 属于 12:00 时段。
2. 尝试取得执行锁。若旧时段实例还在执行，核对它的 PID、创建时间和锁文件，请求退出；等待超时后强制终止。确认锁已释放后才继续。若等待期间跨过时段边界，按实际开始时刻重新计算当前时段，不去执行已经过去的时段。
3. 打开 SQLite，检查过去的记录：旧 running 改为 interrupted；被本轮终止在邮件阶段的旧实例也记中断，但保留已保存的网页结果和原检查结束时间。再找出历史漏跑，只记录，不补跑。
4. 提交本轮 running，登记该实例对应的运行编号，检查当前目标并保存结果。随后处理汇总邮件及投递结果。
5. 关闭数据库，登记整轮已结束，释放锁，返回结果。整轮操作期间都持有执行锁。

这次用户审阅将“旧实例未结束则跳过新轮”改为“先终止旧实例，再执行当前轮”。例如 A 属于 12:00 时段，到 12:05 还卡着，B 会终止 A，把 A 记为未完成执行，然后只检查 12:05 这一轮；B 不继续或重新执行 A。

停止旧实例的参数为 `schedule.stop_timeout_seconds`，默认 5 秒：先发送 SIGTERM，请求退出并最多等 5 秒；未释放锁再发送 SIGKILL，最多再等 5 秒。仍无法取得锁则返回 2，不修改数据库、不并行执行。该参数可省略，现有真实配置继续使用默认值，不需要为此重写文件。

为了避免错误终止和重复启动，还做了以下检查：

- 同一时段的重复启动返回 3，不终止正在工作的同一轮；已经有运行记录时也不再次检查。
- PID 的创建时间必须匹配，且进程确实打开了本项目的锁文件。身份信息损坏、权限不足或不匹配就返回 2，不猜 PID。
- 不终止当前进程或它的父辈进程，相关调用返回 3。
- 两个新实例同时启动时，短暂持有 `.startup` 锁的实例负责处理旧任务，另一个返回 3，避免互相终止。

运行目录会出现执行锁文件、`.startup` 文件、`.owner.json`（以及写入期间短暂存在的 `.owner.json.tmp`）。JSON 只记 PID、创建时间、时段、运行编号和整轮是否已结束，不保存配置或密码。先写临时文件再替换 JSON，避免读到只写了一半的信息；执行锁文件本身不删除。进程退出后内核自动释放锁，文件留在磁盘是正常的。若身份信息损坏，先检查并停止旧实例，不要直接删除锁文件绕过检查。

同一数据库必须共用同一路径的锁；初版用于本机文件系统，不协调多机。进程身份检查与信号发送使用用户批准的 psutil；终止和强制结束方法会在发信号前检查 PID 是否重新使用，参考 [psutil 文档](https://psutil.readthedocs.io/stable/index.html)。

| 退出码 | 含义 |
| --- | --- |
| 0 | 本轮网站最终健康，且无执行错误；过去的中断或漏跑仍会汇总 |
| 1 | 本轮至少一个目标重试后仍失败 |
| 2 | 配置、存储、邮件或停止旧实例失败等执行错误；优先于网站故障 |
| 3 | 同一时段重复启动、另一新实例正在处理接替，或试图终止自身/父辈进程 |

`completed` 表示网页检查结果已保存，不保证后面的邮件阶段也完成。若旧实例在发信过程中被终止，下一轮将它改为 interrupted，并以事件发生时间记录中断，已保存的检查时间和明细不丢弃，仍能用于报告最新网站状态。SMTP 接收与数据库标记不是一个事务，两步之间退出可能重复发信。

## 手动运行与启动器

先填写被忽略的 `src/website_healthcheck/config/config.yaml`，然后在仓库根目录执行以下命令。主入口已在 pyproject.toml 中注册为 website-healthcheck，不是根目录的 main.py：

```sh
uv sync --locked
uv run --locked website-healthcheck
```

也可通过 `--config /absolute/path/config.yaml` 指定配置。命令现在会真实执行检查和可能的邮件发送，不再只是配置校验；示例中的密码不能用于实际投递。

cron 使用 `/bin/sh scripts/run_healthcheck.sh /absolute/uv /absolute/config.yaml`。启动器从脚本位置确定仓库目录，设置 UTC 和 UTF-8 输出，使用 `uv run --locked --no-sync` 调用已安装入口，以 exec 保留退出码。它不依赖交互式 shell 的工作目录或 uv PATH；部署及更新依赖后要先重新 `uv sync --locked`，运行时不自动同步环境。

用 `command -v uv` 找到 uv 路径。启动器的两个参数必须是绝对路径，项目、配置和 uv 路径都支持空格、中文和引号。脚本通过 `/bin/sh` 调用，无须额外添加执行位。

## 生成与手动安装 cron

以下为部署模板，将 `/srv/website-healthcheck` 和 `/absolute/uv` 替换为部署机的真实绝对路径；不要直接照抄占位路径。配置与日志路径保留在包内 config/，var/ 运行数据已被 Git 忽略。

```sh
mkdir -p /srv/website-healthcheck/src/website_healthcheck/config/var
chmod 700 /srv/website-healthcheck/src/website_healthcheck/config/var
chmod 600 /srv/website-healthcheck/src/website_healthcheck/config/config.yaml
touch /srv/website-healthcheck/src/website_healthcheck/config/var/healthcheck.log
chmod 600 /srv/website-healthcheck/src/website_healthcheck/config/var/healthcheck.log
uv run --locked python scripts/print_cron.py \
  --uv /absolute/uv \
  --config /srv/website-healthcheck/src/website_healthcheck/config/config.yaml \
  --log /srv/website-healthcheck/src/website_healthcheck/config/var/healthcheck.log
```

辅助脚本只读配置并打印，不创建运行文件或安装任务。默认检查周期生成如下结构，实际路径会自动进行 shell 引用：

```cron
CRON_TZ=UTC
*/5 * * * * /bin/sh /srv/website-healthcheck/scripts/run_healthcheck.sh /absolute/uv /srv/website-healthcheck/src/website_healthcheck/config/config.yaml >> /srv/website-healthcheck/src/website_healthcheck/config/var/healthcheck.log 2>&1
```

先用 `crontab -l` 核对已有任务，再用 `crontab -e` 手动加入生成片段，保留原有条目。`CRON_TZ` 会作用于后续条目，安装时需保留其他任务原有时区设置，或将此片段放在现有条目末尾。不要把生成结果直接管道传入 crontab，以免覆盖已有任务。

时区必须在**调度器**层面保证：支持 `CRON_TZ` 的 Cronie 可直接使用片段；Debian cron 按守护进程的统一时区调度，应由部署环境将其设为 UTC。启动器内的 `TZ=UTC` 只影响子进程，不能改变 cron 何时触发。Mac 开发可直接运行启动器验证流程，不假设 Mac cron 支持 `CRON_TZ`。依据：[Cronie crontab 手册](https://man7.org/linux/man-pages/man5/crontab.5.html)、[Debian cron 手册](https://manpages.debian.org/bookworm/cron/cron.8.en.html)。

周期从 config 的 schedule.interval_minutes 读取，必须为 60 的正因数；60 分钟生成 `0 * * * *`。修改周期后重新生成并更新 cron。cron 对 `%` 有独立解析规则，辅助脚本拒绝包含百分号、换行或空字符的路径；普通空格与引号正常支持。日志不能与配置、数据库、锁及进程信息文件重合；数据库也不能与锁及其辅助文件重合。

cron 行将 stdout/stderr 追加到指定日志；日志父目录需预先创建，已有文件权限应设为 0600。部署时用系统 logrotate 等工具配置轮转，初版不自动清理日志或数据库历史。

## 验证范围

步骤 5 使用真实 SQLite 和操作系统 flock，以 HTTPX MockTransport、邮件函数替身及受控 UTC 时钟验证主流程。子进程测试验证已安装入口、真实 uv 启动器、跨工作目录、旧实例终止及接替、强制退出后的锁释放；真实 shell 验证参数引用、日志追加和退出码保留。没有安装系统 crontab，没有连接真实网站或 Gmail；步骤 6 补充本机 HTTP 服务测试及完整 pytest workflow，见 [统一测试说明](testing.md)，部署维护及可选 Mac 验证见 [部署说明](deployment.md)。

2026-10-18，本机 Python 3.12：按本轮审阅修改后，入口、锁与 cron 相关 74 项测试，全套 `uv run --locked pytest -q` 共 240 项通过。新增覆盖正常终止、忽略 SIGTERM 后强制结束、同一时段保护、身份损坏/PID 重用/不匹配不发信号、无权限、无法确认停止、两个新实例争用，以及旧轮卡在检查或邮件阶段后的真实接替。`/bin/sh -n scripts/run_healthcheck.sh` 通过；sdist/wheel 构建通过，源码归档包含两个辅助脚本，wheel 包含 scheduler 子包。构建规则排除本地 `.vscode/`，归档未包含真实 config.yaml、数据库、锁或日志；开发者的编辑器文件保持原样。
