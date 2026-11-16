# 启动脚本

在 Finder 或资源管理器中打开对应系统的文件夹，双击需要的脚本。终端运行时可以使用完整路径；脚本不依赖当前工作目录。

| 操作 | macOS（`.command`） | Windows（`.cmd`） |
|---|---|---|
| 打开资料库 | `macos/open-library.command` | `windows/open-library.cmd` |
| 更新照片索引 | `macos/update-photos.command` | `windows/update-photos.cmd` |
| 同步公开网页 | `macos/sync-website.command` | `windows/sync-website.cmd` |
| 首次安装或更新依赖 | `macos/setup-mac.command` | 按根目录 README 安装 Python 依赖 |
| 停止后台服务 | `macos/stop-library.command` | — |
| 恢复 v1 | 按 `docs/macos.md` 的恢复说明操作 | `windows/restore-v1.cmd` |

macOS 网页同步脚本可加 `--prepare-only`，仅在本地准备和验证。实际同步会发布网页；版本恢复会更改源码，请先阅读项目根目录的 `docs/versions.md`。
