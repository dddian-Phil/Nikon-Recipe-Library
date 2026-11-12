# Mac 使用与 Windows 迁移

## 日常使用

在 Finder 中打开项目，双击 `open-library.command`。照片放在 `photos/`，收藏和关联保存在 `local/personal-records.json`。网页操作、8 秒自动扫描、云创手动同步和 GitHub 网页发布沿用原来的功能。

- `setup-mac.command`：首次安装或更新 Python 依赖，需要联网并已安装 uv。
- `open-library.command`：启动本项目服务并打开浏览器；已有服务时重新生成页面后复用。
- `update-photos.command`：立即扫描本地照片。
- `stop-library.command`：停止本项目启动的后台服务；扫描、同步或发布期间会提示等待。
- `sync-website.command`：备用网页发布入口，等同于在本地网页主动发布。

运行 `setup-mac.command` 后无需激活 Python 环境。脚本使用项目内的 `.venv`，不会使用从 Windows 复制的 Conda 路径。端口从 `local/runtime.json` 读取，默认 8765。日志在 `local/runtime.log`。关闭浏览器不会停止后台服务。

这些入口也可以在终端使用，路径包含空格或中文、终端当前目录不是项目目录时同样有效。例如在项目根目录执行：

```bash
./setup-mac.command
./open-library.command
./update-photos.command
./stop-library.command
```

## 分支与目录

| 分支 | 用途 |
|---|---|
| `main` | 保留迁移前的主分支 |
| `claude/ui-polish` | 保留 GitHub 上原有的探索分支 |
| `codex/windows/main` | Windows 主目录最后的提交 |
| `codex/windows/claude/ui-polish` | Windows 本地探索分支最后的提交，比原远程分支多 8 个提交 |
| `codex/windows/codex/static-deploy` | Windows 网页发布分支的固定保存点 |
| `codex/windows/claude/kind-goldwasser-bac032` | Windows 的另一个探索分支保存点 |
| `codex/windows-ui-exploration` | 从 Windows 探索目录找回的未提交界面改动和测试 |
| `codex/macos` | 当前项目目录，Mac 日常使用和后续开发 |
| `codex/macos-exploration` | `local/worktrees/ui-polish/`，保留探索界面并合入 Mac 启动支持 |
| `codex/static-deploy` | `local/worktrees/static-deploy/`，沿用现有 GitHub Pages 发布流程 |

生产发布分支继续使用 `codex/static-deploy`，以匹配现有 Actions 工作流。Windows 当时的版本另外保存在 `codex/windows/codex/static-deploy`，后续发布不会改变这个保存点。`v1`、`v1-web`、`v2`、`v2-web` 标签保留。迁移本身不推送或发布网站。

照片、个人记录与完整官方素材集中在主项目目录。探索 worktree 保留源码和独立记录空间，不额外复制整套照片库。需要探索真实数据时，先明确导入或预览方式，避免两边分别修改收藏后互相覆盖。

## 网页发布

在本地网页的「资料与备份」中点击「同步到 GitHub 网页」。发布程序本身使用 Python，并支持 macOS 文件锁，不需要安装 PowerShell。需要 GitHub CLI 已登录，且 Git 能正常推送。首次配置可以检查：

```bash
gh auth status
```

只在本地导出、校验，不提交或上传：

```bash
./sync-website.command --prepare-only
```

这个操作会在部署 worktree 生成公开快照与 `dist/`。完整部署规则见该目录的 `docs/deployment.md`。

其他电脑重新建立部署目录时，在主项目根目录执行下面的命令；本机已建立，不要重复运行：

```bash
git worktree add local/worktrees/static-deploy codex/static-deploy
```

## 迁移保留的资料

2026-11-12 迁移保留了主目录个人记录、原始照片、完整官方素材、Excel、预览，以及 `local/versions/` 中的旧版恢复包。探索目录另外发现的 3 张原照片也已加入 `photos/`，同名同内容的照片只保留一份。

主目录的个人记录更新时间较新，继续作为当前记录。探索目录的不同版本另存于 `local/migration/2026-11-12/exploration-records/`，不自动合并收藏。迁移时的主目录原始记录在同级 `main-records/`，旧运行配置也保存在这里。

逐文件的 SHA256、去向与清理分类见同级 `manifest.json`。历史开发脚本、诊断、旧源码片段和本地工具配置保存在 `windows-development-notes.zip`。重复工作目录、旧构建产物、浏览器截图、Python 缓存和 Windows worktree 绝对路径不作为运行环境保留。Git 分支和 Codex 检查点已导入当前仓库。

以上个人资料和迁移档案都位于 Git 忽略的目录，不会随着代码提交上传。换电脑时仍需单独备份 `photos/`、`local/` 和 `data/catalog/`。

## 维护与恢复

Mac 上使用项目解释器：

```bash
./.venv/bin/python -B -m unittest discover -s tests -v
./.venv/bin/python -B app/build_guide.py

# 只检查 v1 恢复点，不修改文件
./.venv/bin/python -B app/restore_v1.py
```

实际成对恢复 v1 的参数是 `--restore`，会先备份两个源码目录。恢复旧版本也会恢复旧版启动支持；Mac 新增入口及开发成果仍保存在 Mac 分支。操作前停止本地服务，细节见 `docs/versions.md`。

Windows 的 `.cmd` 和 PowerShell 脚本继续保留。Mac 安装脚本下载的 Python 位于 `local/python/`，依赖缓存位于 `local/uv-cache/`；这些目录和 `.venv/` 可以在重建环境时重新生成。
