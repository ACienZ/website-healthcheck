# GitHub Release 发布

项目当前准备版本为 `0.2.0`，完整改名为 website-healthcheck；标签与 Release 尚未创建。[v0.1.1 Release](https://github.com/ACienZ/website-healthcheck/releases/tag/v0.1.1) 已于 2026-10-18 发布，保留其原始名称、标签与归档。版本 PR 合并、验收后才发版。使用 GitHub Actions 和 GitHub Release，不发布到 PyPI；下载内容使用 GitHub 为标签提供的源码 ZIP/tar.gz，不额外上传 wheel 或运行数据。

## v0.2.0 发布准备

- [Issue #26](https://github.com/ACienZ/website-healthcheck/issues/26) 同步仓库、Python 分发名、包名、命令、通知标题及文档，版本从 0.1.1 更新为 0.2.0，不升级依赖。包含已合并 PR #25 的展示首页。
- 新入口为 `website-healthcheck`，导入为 `website_healthcheck`；旧部署需要迁移配置/运行数据并更新 cron，见[迁移说明](../README_CN.md#从旧名称迁移)。
- 用户已要求最后发布新版本；仍按开发流程先审阅合并改名 PR，再同步 main、整理本地、验证实际合并提交并推送 `v0.2.0`，最后核实 Actions、Release 与 GitHub 源码归档。
- 下方 v0.1.1 准备与 v0.1.0 发布记录为历史资料；不重新创建或移动已有标签。

## v0.1.1 发布准备

[PR #21](https://github.com/ACienZ/website-healthcheck/pull/21) 已将 MIT LICENSE、包许可证元数据和文档收尾合并到 main（cbd135b）。旧 v0.1.0 快照不含这些文件，因此通过 [Issue #22](https://github.com/ACienZ/website-healthcheck/issues/22) 准备新版本：

- 将 pyproject.toml 与 uv.lock 的项目版本更新为 0.1.1，保留现有依赖和业务行为。
- 新源码归档应包含 LICENSE，包元数据应声明 MIT；构建检查同时确认真实配置与运行数据仍被排除。
- 用户合并版本 PR 后，在实际合并提交上验收并推送 v0.1.1，由现有 Actions 测试通过后创建新的正式 Release，再核对源码下载内容及 Latest 标记。
- 保留 v0.1.0 标签和历史归档，不重新指向新提交。此处记录发布准备，不代表 v0.1.1 已经发布；实际结果以仓库 Release 和对应 Actions 为准。

## v0.1.0 正式发布记录

2026-10-18 已完成正式发布：

- 用户合并 [PR #19](https://github.com/ACienZ/website-healthcheck/pull/19) 后授权发版；标签 v0.1.0 指向合并提交 `76ada0b14cc5247333e0cd13f21088cae4de7fba`。
- 实际合并提交本地 `uv sync --locked` 成功，完整 pytest 271 项通过（13.64 秒）；发布前标签校验通过。
- [Release Actions](https://github.com/ACienZ/website-healthcheck/actions/runs/39096892581) 的 Linux 完整 pytest 271 项通过（7.75 秒），随后 publish job 成功。
- [v0.1.0 Release](https://github.com/ACienZ/website-healthcheck/releases/tag/v0.1.0) 于 04:23:26 UTC（13:23:26 JST）发布，非草稿、非预发布；自动说明已核对。
- 下载实际 GitHub 源码 tar.gz 检查，共 50 个文件，必要源码、锁文件、双语文档及示例配置齐全；无真实 config.yaml、数据库、日志、.vscode/ 或 .venv/。
- Issue #11 已关闭，Project 为 Done；同一提交的 main 与标签普通 Tests CI 也均通过。

公开前已通过 PR #21 添加 MIT LICENSE 并同步完成记录；v0.1.0 标签及其源码快照保持不变。包含许可证的新归档通过上述 v0.1.1 版本发布。

## 什么时候会发布

`.github/workflows/release.yml` 只在推送 `v*` 标签时运行。普通 commit、分支推送和 PR 合并不会直接发版。`docs:`、`feat:` 等提交前缀用于说明改动，不会自动决定或修改版本号；升级时仍须在独立 PR 中同步 `pyproject.toml` 和 `uv.lock`。

发布分成两项任务：

1. `verify-and-test` 只有仓库读取权限。检出标签并取回完整 Git 历史，用 `scripts/verify_release.py` 确认标签等于 `v` 加包版本、当前检出提交就是标签提交、提交已进入 `origin/main`。然后执行 `uv sync --locked` 和 `uv run --locked pytest`。
2. `publish` 依赖第一项成功后才启动，并单独取得 `contents: write`。先读取完整 Release 列表；同名 Release 已存在就保留，否则运行 `gh release create --verify-tag --generate-notes`，由 GitHub 根据历史生成发布说明。

main 可能已有更新，所以标签不必指向 main 最新提交，但必须指向它历史中的提交。普通标签和带说明的标签都支持；下面的发布步骤使用带说明的标签。

任意校验、依赖安装或测试失败都会跳过 `publish`。查询 Release 的网络、认证或权限错误也会停止，不能当成“版本不存在”。同一标签的运行排队，重复执行不覆盖现有说明或资产。workflow 不需要额外配置 PAT 或 Gmail Secrets，使用本次运行的 `GITHUB_TOKEN`；仓库或组织的权限策略仍须允许创建 Release。

权限、任务依赖和标签触发规则见 [GitHub Actions 官方说明](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax)；`--verify-tag` 要求标签已经存在，`--generate-notes` 调用 GitHub 自动生成说明，见 [GitHub CLI 文档](https://cli.github.com/manual/gh_release_create)。Release 列表使用 [gh api 的分页功能](https://cli.github.com/manual/gh_api)，避免只检查第一页。

## 合并后的发布操作

**以下命令用于 v0.2.0，仅在对应版本 PR 已合并、验收完成后执行。用户已要求最终发版；v0.1.0 和 v0.1.1 已发布，不重复创建或移动旧标签。**

1. 确认工作区没有未提交的项目改动，再切换到 main 并同步：

   ```sh
   git fetch --prune origin
   git switch main
   git pull --ff-only origin main
   uv sync --locked
   uv run --locked pytest
   ```

2. 核对版本与已有标签，确认目标提交就是打算发布的代码。不要移动或覆盖已经发布的标签：

   ```sh
   uv run --locked python -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])'
   git log -1 --oneline
   git tag --list v0.2.0
   gh release list --repo ACienZ/website-healthcheck
   ```

3. 获得授权后创建标签，先在本地验证，再只推送这个标签：

   ```sh
   git tag -a v0.2.0 -m "Release v0.2.0"
   uv run --locked python scripts/verify_release.py v0.2.0
   git push origin v0.2.0
   ```

4. 打开仓库 Actions，查看名称为 `Release` 的运行：先确认 `verify-and-test` 完整 pytest 通过，再确认 `publish` 成功。也可以用以下只读命令查看：

   ```sh
   gh run list --repo ACienZ/website-healthcheck --workflow release.yml --limit 5
   gh release view v0.2.0 --repo ACienZ/website-healthcheck
   ```

5. 打开 Release 页面，检查标签、目标提交、自动生成说明和源码下载链接。自动说明是否完整仍需人工查看，尤其是首次发布；必要时在 GitHub 页面编辑说明，不重打标签。

## 失败和重复执行

- 版本不同：检查标签和 `pyproject.toml`，通过正常 PR 修正；不要为了绕过校验改已发布标签。
- 提交不在 main：先完成 PR 合并，再按已合并提交准备发布。squash merge 会创建新提交，不能给合并前的功能分支提交打标签。
- pytest 失败：Release 不会创建。修复后合并，通过正常版本发布流程重新验收，不绕过测试门禁。
- 网络、依赖下载或 GitHub 权限等临时故障：解决原因后，可以在该次 Actions 页面选择 Re-run failed jobs；若重跑全部任务，会再次运行完整测试。
- 已有 Release：直接跳过创建，保留原说明和资产，不执行更新或删除命令。已有草稿也按“已存在”处理，需要人工确认其后续状态。

本地 `uv run --locked pytest tests/test_release.py -v` 使用临时 Git 仓库和假 `gh` 验证发布判断及失败停止；不会在本项目打标签或访问 GitHub。完整标签触发、Actions 写权限和 Release 创建已通过上述首次正式发布验证；后续版本仍需核对对应标签的实际 Actions 结果。
