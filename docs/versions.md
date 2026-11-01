# 版本保存与成对恢复

改版前的所有已有修改已经提交；本地和 GitHub 均保存了下面两个配套标签。

| 部分 | 分支 | v1 标签 | 完整提交 |
| --- | --- | --- | --- |
| 本地完整资料库 | main | v1 | 81901f7c8e7ca0eb9782676d63e3810361344d34 |
| 线上展示页 | codex/static-deploy | v1-web | 39c85361bf9a58dbe611c26023a7f3ebe381b61d |

GitHub 地址：https://github.com/dddian-Phil/Nikon-Recipe-Library/tree/v1 和 https://github.com/dddian-Phil/Nikon-Recipe-Library/tree/v1-web 。标签固定保留，不随分支的新提交移动。

## 本机恢复

关闭本地资料库服务与其他正在编辑本项目的程序，再双击根目录的 `restore-v1.cmd`。它会：

1. 核对两个标签的完整提交，以及所有恢复文件的路径。
2. 先把即将覆盖的两个目录中的现有文件备份到 `local/versions/before-v1-restore-日期时间/current-both-projects.zip`，核验备份后才恢复。
3. 从 `v1` 恢复主项目，从 `v1-web` 恢复 `local/worktrees/static-deploy`，并重新生成本地浏览页。

原始照片、个人收藏、关联记录和运行配置不参与回退。v1 之后新增、但 v1 中不存在的文件会保留；不会删除任何用户文件。工具不会切换分支、重置 Git 历史、推送或发布。重新打开 `open-library.cmd` 查看本地 v1。

只核验恢复点、不恢复：`powershell -NoProfile -File app/restore-v1.ps1 -CheckOnly`。

## 同时恢复 GitHub 与线上展示

本机恢复后，两边源码已经恢复；线上页面需要把恢复结果分别提交、推送，再由 Pages 重新部署才会改变。两个分支一起处理，不要只恢复其中一个。操作前先保存恢复工具和这份说明，并确认没有其他人的进行中修改。

在根目录检查并提交主项目的恢复结果：

```powershell
git diff --stat
git add -u
git commit -m "Restore local library to v1"
git push origin main
```

再检查并提交展示分支的恢复结果：

```powershell
git -C local/worktrees/static-deploy diff --stat
git -C local/worktrees/static-deploy add -u
git -C local/worktrees/static-deploy commit -m "Restore public website to v1-web"
git -C local/worktrees/static-deploy push origin codex/static-deploy
```

这会创建新的恢复提交，保留新版历史。等待对应 Pages 工作流成功，再刷新线上页面核对；不要用强制推送来回退。若之后仍想把最新照片和收藏同步到恢复后的页面，可以再使用正常的「同步到 GitHub 网页」。

## 独立备份

`local/versions/v1-20261101/v1-project.zip` 保存了改版前的本机代码、生成页面、说明和测试；`v1-web.zip` 是展示分支的完整 v1 Git 快照，包含公开展示图。两份压缩包都经过完整性检查，存放于忽略目录，不会公开上传个人状态。
