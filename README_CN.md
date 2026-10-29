# Python 网站健康检查练习

[English](README.md) | 简体中文 · [MIT 许可证](LICENSE)

Canlah Healthcheck 是一个单机 Python 工程练习，串联 HTTP 检查与重试、SQLite 执行记录、漏跑与中断检测、Gmail 汇总告警，以及 pytest / GitHub Actions 验证。每次启动只执行当前这一轮，五分钟后的下一轮由系统 cron 再次启动。

可以从[安装与测试](#安装与首次运行)开始体验，无需访问真实网站或 Gmail。练习重点是模块职责划分、执行与通知状态的持久化、进程协调和隔离测试。当前实现尚未达到生产就绪，详见[当前边界](#当前边界与生产准备)。`docs/` 下的详细文档目前使用中文。[原始练习要求](refs/requirement.md)保留为历史资料，其中的简短 README 限制不再适用于当前展示文档。

## 内容导航

- [功能与检查规则](#功能与检查规则)
- [技术栈](#技术栈)
- [目录与模块职责](#目录与模块职责)
- [一轮任务如何运行](#一轮任务如何运行)
- [安装与首次运行](#安装与首次运行)
- [配置管理](#配置管理)
- [每五分钟部署运行](#每五分钟部署运行)
- [测试与持续集成](#测试与持续集成)
- [版本发布](#版本发布)
- [数据维护与故障排查](#数据维护与故障排查)
- [当前边界与生产准备](#当前边界与生产准备)

## 功能与检查规则

默认目标 `https://canlah.ai/` 和 `https://canlah.ai/playbook/` 沿用原始练习示例，可通过配置修改 URL 和检查规则。每个页面都要满足：

| 检查项 | 默认通过条件 |
| --- | --- |
| 状态码 | 跟随重定向后的最终 HTTP 状态为 200 |
| 响应时间 | 从发送请求到完整读完响应正文不超过 5 秒，包含重定向 |
| 页面内容 | 响应文本包含区分大小写的 `Canlah`，不执行 JavaScript 或解析 DOM |

任何条件失败都会重试，默认最多额外重试 2 次、间隔 2 秒，即最多请求 3 次。某个目标最终失败后，仍继续检查其他目标；重试后恢复则保留失败尝试，但不产生最终故障告警。整轮至少一个目标最终失败时，命令以非零状态退出。

HTTP 等待超时与上述 5 秒判断分开配置：`request_timeout_seconds` 默认 10 秒，约束 HTTPX 各阶段的等待；它不是整轮任务的总时限。收到完整响应后，再用实际耗时判断是否超过 5 秒。

除页面检查外，还提供：

- 执行记录：保存每轮开始、结束、状态、页面最终结果及每次实际尝试。
- 漏跑检测：下次成功启动时找出过去根本没启动的时段，只记录并告警，不重新请求历史任务。
- 中断检测：确认旧实例已退出后，给没完成的旧轮记下中断；新时段遇到卡住的旧实例，会先核对身份并终止它，再执行当前轮。
- 汇总邮件：当前检查全部结束后，汇总未通知的故障、漏跑、中断和各目标最新状态；故障后来恢复也不会丢掉历史告警。
- 通知记录：发送成功才标记本批事件；失败保留，并按配置的最小邮件间隔再次尝试。

## 技术栈

| 部分 | 选型与用途 |
| --- | --- |
| 语言与环境 | Python 3.12、uv；依赖声明在 pyproject.toml，版本锁定在 uv.lock |
| 项目布局与构建 | src 包布局、Hatchling；安装后提供 canlah-healthcheck 命令 |
| 配置 | YAML、PyYAML、Pydantic v2；读取配置并验证类型、路径与数值范围 |
| HTTP | HTTPX 同步客户端；顺序检查目标并复用连接 |
| 数据库 | Python 标准库 sqlite3；本机文件，无需独立数据库服务或 ORM |
| 调度与进程 | Linux cron、POSIX shell、fcntl.flock、psutil；控制启动与旧实例终止 |
| 告警 | 标准库 smtplib、ssl、email；Gmail SMTP_SSL，默认 465 端口 |
| 测试与自动化 | pytest、GitHub Actions；离线测试、本机 HTTP 和真实子进程测试 |

初版不使用消息队列，开发环境为 Mac，目标部署环境为 Linux。

## 目录与模块职责

```text
.
├── README.md                    # 英文项目介绍与使用说明
├── README_CN.md                 # 中文项目介绍与使用说明
├── AGENTS.md                     # 协作约定
├── TODO.md                       # 任务进度及验收记录
├── pyproject.toml / uv.lock      # 包配置、依赖与锁文件
├── refs/requirement.md           # 历史练习要求
├── src/canlah_healthcheck/
│   ├── main.py                  # 命令入口，串联下面各模块
│   ├── config/
│   │   ├── load_config.py       # 配置模型、读取、校验及路径解析
│   │   ├── config.yaml.example  # 可提交的无凭证示例
│   │   ├── config.yaml          # 用户本地真实配置，Git 忽略
│   │   └── var/                 # 默认数据库及锁目录，只跟踪 .gitkeep
│   ├── checker/checker.py       # 页面检查、计时、判定与重试
│   ├── scheduler/process_lock.py # 核对旧进程、终止与取得执行锁
│   ├── storage/
│   │   ├── schema.py           # 七张表的定义与字段用途
│   │   ├── models.py           # 事件、已存检查及邮件报告的数据结构
│   │   └── storage.py          # 读写记录、核对漏跑/中断、通知状态
│   └── notifier/notifier.py    # 生成汇总邮件并发送，不直接修改数据库
├── scripts/
│   ├── run_healthcheck.sh      # cron 启动器，设置目录与环境后执行命令
│   ├── print_cron.py           # 根据配置打印 cron 条目，不自动安装
│   └── verify_release.py       # 发布前核对标签、包版本和 main 历史
├── tests/                     # pytest 单元及集成测试
├── docs/                      # 模块、部署、测试及设计的详细说明
└── .github/workflows/         # ci.yml 运行 pytest，release.yml 验证并发布版本
```

| 模块 | 主要接口 | 职责边界 |
| --- | --- | --- |
| config | load_config | YAML 转成经过验证的配置对象，不创建运行数据 |
| checker | check_page、run_checks | 返回最终结果和全部尝试，不写库或发邮件 |
| scheduler | process_lock | 保证只有取得执行锁的实例进入业务流程 |
| storage | start_run、finish_run | 分别保存开始记录与完整检查结果 |
| storage | reconcile_schedule | 判断过去哪些轮中断、哪些时段没启动，不补跑 |
| storage | pending_report、record_delivery | 查询待通知快照，保存发送尝试与成功标记 |
| notifier | send_alert | 把报告转换成邮件并发送，不决定间隔或写库 |
| main | main、_run_once | 安排调用顺序、持有锁并给出退出码 |

阅读代码可从 [main.py](src/canlah_healthcheck/main.py) 开始，再根据上表进入模块。更细的接口与设计见 [实施设计](docs/planning/implementation-plan.md)。

## 一轮任务如何运行

1. **加载配置。** `main()` 解析 `--config`，调用 `load_config()`；无效配置返回 2，不开始检查。
2. **确定当前时段并处理旧实例。** `_run_once()` 按 UTC 和检查周期计算时段，调用 `process_lock()`。旧时段实例仍在运行时，核对 PID、创建时间及打开的锁文件，请求退出；超时再强制终止。确认拿到锁才继续，无法确认则返回 2。同一时段重复启动等保护情况返回 3。
3. **检查过去发生了什么。** 打开 `Storage`，按实际拿锁后的时间重算当前时段，调用 `reconcile_schedule()`：把旧的未完成任务记中断，找出历史漏跑，不重新执行它们。
4. **保存本轮开始。** `start_run()` 先提交 `running` 记录，登记本实例的运行编号。这样请求期间崩溃也有记录可查；同一时段已有记录就不再检查。
5. **执行当前检查并保存。** `run_checks()` 顺序检查全部目标；`finish_run()` 在同一数据库事务中保存结果、各次尝试、最终故障事件，并把运行改为 `completed`。
6. **处理汇总邮件。** `pending_report()` 判断是否有待通知事件、是否到了邮件最小间隔。有报告才调用 `send_alert()`；之后 `record_delivery()` 记录结果。成功标记本批已通知，失败保留并返回执行错误 2。
7. **结束本轮。** 关闭数据库、登记实例已结束并释放锁。本轮网站健康返回 0，有最终网站故障返回 1。下一轮由 cron 启动，不在此进程里等待。

例如 12:00 的 A 到 12:05 仍未结束，12:05 的 B 会先确认并停止 A，再记下 A 中断，执行 B 的当前轮。B 不继续 A，也不重跑 A。若 A 已保存检查结果、卡在发邮件阶段，结果仍保留，但该实例的中断也会被记录。

数据库状态的含义：

| 状态 | 表示什么 |
| --- | --- |
| running | 已保存开始记录，尚未保存完成结果；单凭此值不能判断进程是否还活着 |
| completed | 页面检查结果已经保存；网站可能失败，之后的邮件也可能失败 |
| interrupted | 确认旧实例已经停止后，发现它没有完整结束；不继续执行旧任务 |

锁覆盖检查、数据库操作和邮件阶段；检查周期与邮件最小间隔分别配置。详细过程及进程保护规则见 [调度说明](docs/scheduling.md)。

## 安装与首次运行

先安装 Git、uv，并确保能下载 Python 及锁定依赖。获取项目后在仓库根目录执行：

```sh
git clone https://github.com/ACienZ/canlah-healthcheck.git
cd canlah-healthcheck
uv python install
uv sync --locked
```

`uv python install` 按 `.python-version` 准备 Python 3.12，`uv sync --locked` 安装项目和测试依赖，不修改锁文件。已有仓库直接从 `uv sync --locked` 开始，不要再次 clone 到项目内部。

### 通过测试体验

安装后执行：

```sh
uv run --locked pytest
uv run --locked canlah-healthcheck --help
```

这个入口无需真实配置或 Gmail 凭证。测试使用临时数据库、HTTP / SMTP 替身、本机回环 HTTP 服务和子进程，不请求真实目标网站或发送邮件。安装依赖需要下载网络。覆盖范围见[测试与持续集成](#测试与持续集成)。

### 使用真实服务运行

如果还没有真实配置，再复制示例；保留已有配置：

```sh
test -e src/canlah_healthcheck/config/config.yaml || cp src/canlah_healthcheck/config/config.yaml.example src/canlah_healthcheck/config/config.yaml
chmod 600 src/canlah_healthcheck/config/config.yaml
mkdir -p src/canlah_healthcheck/config/var
chmod 700 src/canlah_healthcheck/config/var
```

编辑 `config.yaml`，填写 Gmail 账号、应用专用密码、发件人和收件人。默认服务器为 `smtp.gmail.com:465`；示例密码只是占位值。运行前检查目标 URL 和判定规则。

```sh
uv run --locked canlah-healthcheck --help
uv run --locked canlah-healthcheck --config "$PWD/src/canlah_healthcheck/config/config.yaml"
echo "退出码：$?"
```

第二条命令会真实请求网站、写库，并在需要时发送邮件。不传 `--config` 时使用安装包内的 `config/config.yaml`；当前 uv 可编辑安装对应源码中的配置。入口是安装时注册的 `canlah-healthcheck` 命令，不是根目录的 `main.py`。

| 退出码 | 含义 |
| --- | --- |
| 0 | 当前网站最终健康，且没有执行错误 |
| 1 | 至少一个目标重试后仍失败 |
| 2 | 配置、数据库、邮件或停止旧实例等执行错误；优先于网站失败 |
| 3 | 同一时段重复启动、另一个新实例正在处理接替，或自身/父辈保护 |

同一个五分钟时段内再次运行可能返回 3；历史漏跑被记录和汇总，不会单独让当前健康的网站检查返回 1。

## 配置管理

所有运行参数通过 [config.yaml.example](src/canlah_healthcheck/config/config.yaml.example) 对应的真实 YAML 管理。模型拒绝未知字段和不合法类型，错误输出不展示密码或完整原始输入。

| 配置段 | 主要内容 |
| --- | --- |
| targets | 要检查的 HTTP(S) URL 列表，非空且不重复 |
| check | 状态码、关键词、完整响应耗时阈值、请求等待超时、重试次数和间隔 |
| schedule | 检查周期、漏跑宽限、终止旧实例的等待时长 |
| storage | SQLite 数据库和执行锁路径 |
| mail | 邮件最小间隔、SMTP 连接参数、账号密码、发件人及收件人 |

检查与邮件默认均为五分钟。`schedule.interval_minutes` 限定为 60 的正整数因数；`grace_seconds` 默认 60 秒，只用于判断过去的漏跑，不是 HTTP 超时。`stop_timeout_seconds` 默认 5 秒，请求退出和强制终止后各最多等待这段时间。

相对数据库和锁路径以配置文件所在目录为基准。默认运行数据在 `config/var/`；同一数据库必须使用同一个执行锁。真实 `config.yaml` 与运行数据不提交，Git 仅跟踪示例及目录占位文件。详细字段、默认值和校验规则见 [配置说明](docs/configuration.md) 与 [设计中的配置表](docs/planning/implementation-plan.md#2-配置与目录约定)。

## 每五分钟部署运行

Linux 上先在目标路径准备配置、执行 `uv sync --locked`，再手动运行一次启动器。以下路径为模板，请替换为实际绝对路径：

```sh
/bin/sh /srv/canlah-healthcheck/scripts/run_healthcheck.sh \
  /absolute/uv \
  /srv/canlah-healthcheck/src/canlah_healthcheck/config/config.yaml
```

启动器会切换到仓库目录，设置 UTC 和 UTF-8，通过 `uv run --locked --no-sync` 执行已安装命令；它不会在每轮重新安装依赖。

预先创建可写日志目录和文件后，在仓库根目录生成 cron 片段：

```sh
uv run --locked python scripts/print_cron.py \
  --uv /absolute/uv \
  --config /srv/canlah-healthcheck/src/canlah_healthcheck/config/config.yaml \
  --log /absolute/healthcheck.log
```

默认输出的结构如下：

```cron
CRON_TZ=UTC
*/5 * * * * /bin/sh /srv/canlah-healthcheck/scripts/run_healthcheck.sh /absolute/uv /srv/canlah-healthcheck/src/canlah_healthcheck/config/config.yaml >> /absolute/healthcheck.log 2>&1
```

用 `crontab -l` 查看现有任务，`crontab -e` 追加生成内容，保留其他任务。`*/5` 在每小时的 00、05、10 等分钟触发；日志父目录必须事先存在。不要把生成结果直接覆盖整个 crontab。

支持 `CRON_TZ` 的 Cronie 可使用以上时区行，其他实现需按 [部署说明](docs/deployment.md) 确认调度时区。Mac 不假定支持此变量，Desktop 权限、休眠及本机测试步骤也见该文档。修改检查周期后须同步修改 cron。

邮件在当前检查全部结束后才处理，不另装一个邮件 cron。首次待通知故障可立即汇总发送；之后发送尝试至少间隔 `mail.interval_minutes`，实际发送仍要等到一次检查结束。SMTP 失败保留待通知事件；成功才标记。没有事件就不发空邮件。

测试结束后，先查看条目，再打开编辑器：

```sh
crontab -l
EDITOR=nano crontab -e
```

只删除调用本项目 `scripts/run_healthcheck.sh` 的完整任务行，保留其他项目的条目。在 Nano 中按 Ctrl+O、回车保存，再按 Ctrl+X 退出。随后执行 `crontab -l`，确认本项目条目已消失；若没有其他条目，提示没有 crontab 也属正常。不要执行 `crontab -r`，它会删除当前用户的全部任务。

若添加过 `CRON_TZ=UTC`，只有确认其他任务不依赖这行时才删除；它可能影响后续任务的时区。删除 cron 条目只会阻止后续启动，已经在执行的实例仍会完成；停用调度不会删除配置、数据库或日志。

## 测试与持续集成

```sh
uv run --locked pytest
uv run --locked pytest tests/test_http_integration.py -v
```

测试使用 pytest fixture、临时目录及 SQLite 隔离数据。单元测试使用 HTTP 和 SMTP 替身；本机 HTTP 集成真实建立回环连接，验证重定向、完整正文耗时、超时及重试；子进程测试验证真实锁、旧实例终止和命令入口。

2026-10-18 的历史本地完整验证记录为 271 项通过；这不是本次文档调整新运行的测试结果。GitHub Actions 在 push 和 pull_request 时使用 Ubuntu/Python 3.12 执行 `uv sync --locked` 和 `uv run --locked pytest`；实际结果请查看对应提交的 Actions 或 PR Checks。测试不依赖真实 Canlah 网站或 Gmail，也不读取用户的真实配置。详细范围与证据见 [测试说明](docs/testing.md)。

## 版本发布

[v0.1.1](https://github.com/ACienZ/canlah-healthcheck/releases/tag/v0.1.1) 已于 2026-10-18 发布，源码归档包含 MIT 许可证；已有 `v0.1.0` 快照没有许可证文件。后续版本沿用同一流程：版本 PR 合并并获得发版授权后，向 GitHub 推送对应标签。Actions 先确认标签提交已经进入 `main`，再安装锁定依赖并运行完整 pytest；全部通过后，才由独立 job 创建 GitHub Release 和自动生成的发布说明。

重复执行会保留已有 Release；版本不匹配、提交未合并、测试失败或查询失败都会阻止创建。使用 GitHub 提供的源码 ZIP/tar.gz，不上传 PyPI，也不额外上传 wheel。发版命令、失败排查及首次发布的授权步骤见 [发布说明](docs/releases.md)。

## 数据维护与故障排查

SQLite 保存七张表：`runs` 记录每轮状态，`checks` 保存页面最终结果，`attempts` 保留各次尝试，`events` 保存待通知事件，`schedule_state` 记录已核对到的调度位置，`deliveries` 和 `delivery_events` 记录通知尝试及其包含的事件。实际重试次数是某次检查的尝试数减一。字段说明见 [SQLite 文档](docs/storage.md)。

运行时间使用 UTC；日志由 cron 追加到指定文件。只读查询、备份、更新部署和具体排查步骤见 [部署维护](docs/deployment.md#查看记录备份与故障排查)。备份或替换数据库、更新依赖前，先暂停本项目 cron 并确认现有实例已经停止。初版不自动轮转日志、清理历史或迁移表结构。

没有邮件时，先确认是否有最终故障、漏跑或中断事件，再检查邮件间隔和 `deliveries`。只有重试期间失败、最终恢复的页面不会产生最终故障邮件。锁文件仍留在磁盘不表示进程仍在运行；不要通过删除锁文件绕过进程检查。

## 当前边界与生产准备

- 本机程序停机时无法自行发信；漏跑只有恢复后才能被发现。生产环境需要独立心跳发现整机或 cron 停摆。
- SMTP 接受不等于已进入收件箱；SMTP 接受后、数据库标记前退出，或部分收件人拒收，均可能造成重复通知。
- 初版为单机、本机文件系统，不协调多机，也不保证整轮严格的总耗时上限。
- 生产前补充凭证管理、告警抑制与升级、日志轮转、数据库保留、备份恢复演练，以及实际网站、Gmail、cron 和目标机器权限验证。

这些生产准备项是后续建议，初版没有自动实现。具体指：

| 项目 | 需要做的操作 | 解决什么问题 |
| --- | --- | --- |
| 独立心跳 | 向另一台机器或外部监控服务报告每轮是否按时完成；超时未收到报告，由对方告警 | 本机停机或 cron 没启动时，本机程序无法给自己发告警 |
| 凭证管理 | 将 SMTP 密码交给部署环境的凭证系统，并限制读取权限、支持轮换；相应调整程序读取配置的方式 | 当前密码仍在本地 YAML，Git 忽略不能防止部署机上泄露 |
| 告警抑制 | 定义重复故障多久提醒一次、哪些相关故障合并、维护期间哪些告警静音 | 减少重复邮件，避免同一个故障持续轰炸收件箱；当前邮件最小间隔只限制频率 |
| 告警升级 | 定义故障持续多久或多严重时，转给备用负责人或另一通知渠道 | 第一位接收人没看到、未处理时，还有人能继续跟进；不是提升程序权限 |
| 日志轮转 | 按日期或大小切换日志，压缩旧文件，限制保留数量 | 避免一直追加的 cron.log 占满磁盘 |
| 数据保留 | 定义检查、尝试、事件及投递历史保留多久，按表关联关系归档或清理，保留未通知故障 | SQLite 初版只增加记录，需要明确磁盘使用和追溯范围 |
| 备份与恢复演练 | 定期备份 SQLite，把副本恢复到隔离目录，检查完整性并确认可以查询及运行 | 备份文件存在不代表损坏或误删后真的能恢复 |
| 真实部署验证 | 在目标机器检查网站的 DNS/TLS/正文、Gmail 认证与实际收件、cron 触发和文件权限，并测试停机后的识别 | 本机 HTTP 和 SMTP 替身无法证明真实部署环境可用 |
