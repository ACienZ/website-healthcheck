# 配置模块使用与验证

## 当前能力

任务 1 已实现 src 包安装、YAML 配置加载和 Pydantic 校验。步骤 5 已将命令入口接入完整执行：运行命令会检查网站、写入 SQLite，并在需要时发送汇总邮件。

任务 1 已经 PR #13 合并。任务 2 的检查函数使用这些配置，具体接口与验证见 [页面检查模块](checker.md)；完整流程与退出码见 [命令与调度说明](scheduling.md)。

## 安装与运行

在仓库根目录执行 `uv sync --locked`。真实配置位于 `src/website_healthcheck/config/config.yaml`，与 `load_config.py`、`config.yaml.example` 同目录。

如果真实配置不存在，可复制示例并填写自己的 Gmail 信息；不要覆盖已有配置。示例密码仅为占位值，校验成功不代表 Gmail 认证成功。真实配置被 Git 忽略，不随构建包分发。

```bash
uv run --locked website-healthcheck --help
uv run --locked website-healthcheck
uv run --locked pytest
```

不传 `--config` 时使用已安装包内 `config/config.yaml`，不受启动工作目录影响；部署或非可编辑安装建议用 `--config /absolute/path/config.yaml` 指定独立的可写配置位置。加载失败返回 2，参数错误由 argparse 返回 2；配置有效后会继续执行检查；当前完整退出码为 0 健康、1 网站故障、2 执行错误、3 重复启动或自身/父辈保护。不要用示例执行真实邮件验证。

主程序入口位于 `src/website_healthcheck/main.py`，pyproject.toml 的命令映射为 `website_healthcheck.main:main`，对应测试为 `tests/test_main.py`。命令名为 `website-healthcheck`；改名后运行 `uv sync --locked` 刷新已安装入口。旧部署迁移见 [README](../README_CN.md#从旧名称迁移)。

## 配置规则与阅读顺序

先阅读 `load_config(path)`：文件 → `yaml.safe_load` 字典 → `Config.model_validate` 对象 → 存储路径解析。再阅读 `Config` 和各 `Settings` 子类。模型使用严格类型，拒绝未知字段；带默认值的段可省略，targets 和 mail 必填。密码用 SecretStr 隐藏默认展示，仍须保护本地文件。

相对数据库和锁文件路径以配置文件所在目录为基准。示例对应 `src/website_healthcheck/config/var/`；仓库只保留 `.gitkeep`，数据库和锁文件不提交。加载器只解析路径，不创建运行文件。

`grace_seconds: 60` 是任务启动宽限：超过计划启动时间 60 秒仍未启动，后续调度模块才判定漏跑。配置层校验它非负且小于检查周期；存储层按此识别漏跑，入口取得锁后才调用恢复检测。它不同于 HTTP 超时和重试间隔。

`schedule.stop_timeout_seconds` 默认 5：新时段发现旧实例未退出时，请求退出后最多等 5 秒，强制终止后再等 5 秒。省略也会使用默认值，所以原有 config.yaml 无须改动。新依赖 psutil 已经用户批准，并由 uv.lock 固定版本。

## 验证记录

2026-10-17，本机 Python 3.12 环境：

- `uv sync --locked`：包安装成功。
- `uv run --locked pytest -q`：59 项通过（55 项配置测试、4 项命令入口测试）。
- `uv build --out-dir /tmp/canlah-task1-dist`：sdist 和 wheel 构建成功；归档内容检查未包含真实 config.yaml 或 SQLite 数据库。
- 测试只读取示例并使用临时配置，覆盖校验边界、路径、异常脱敏和已安装命令从其他目录运行。

以上为任务 1 当时的验证记录；后续进度见 [调度验证](scheduling.md) 和 [统一测试说明](testing.md)。未进行真实外网 HTTP 或 Gmail 验证。步骤 6 已将 workflow 改为完整 pytest，远端执行结果须在提交推送后核对；安装与维护见 [部署说明](deployment.md)。
