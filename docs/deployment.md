# GitHub Pages 部署与更新

手机访问：<https://dddian-phil.github.io/Nikon-Recipe-Library/>

这是公开配方与官方样片的静态站。电脑无需保持开机。网页版不提供收藏、收藏导入导出、个人照片或个人照片原图访问；这些功能继续在本地完整版使用。筛选条件和图片布局可由浏览器记住，不保存个人收藏。

## 分支和独立目录

生产部署分支为 `codex/static-deploy`。GitHub Actions 在该分支收到 push 时自动构建并部署；只有这个分支允许工作流执行生产部署。普通 `main` 开发不会直接改变线上网站。

本机部署 Worktree 位于原项目根目录下的 `local/worktrees/static-deploy/`。原目录继续使用 `main`，个人照片、收藏和运行配置仍放在原目录。部署目录处于原仓库的忽略目录内。

以下命令从原项目根目录的 PowerShell 执行。不要在另一个会话的工作区直接切换分支。

## 更新官方配方

1. 在原项目打开本地完整版，点击「同步云创」，等待同步成功。
2. 在独立部署目录导出公开快照并构建：

```powershell
Set-Location .\local\worktrees\static-deploy
git branch --show-current
# 应显示 codex/static-deploy
python -B app/build_static.py --export --catalog-source ..\..\..\data\catalog
python -B -m unittest discover -s tests -p test_static_build.py -v
node --check app/app.js
git diff --stat
git add -- data/public-catalog
git diff --cached --stat
git commit -m "Refresh public Nikon catalog snapshot"
git push origin codex/static-deploy
```

导出操作只读取指定官方目录，不会触发 Nikon 请求。若本地同步仍在运行，导出会拒绝不一致快照；等同步结束后重试。没有数据变化时不需要提交。

到仓库的 [Actions 页面](https://github.com/dddian-Phil/Nikon-Recipe-Library/actions) 查看 `Deploy recipe library to GitHub Pages`，等待部署成功后在手机刷新。浏览器页面底部显示官方目录时间。

## 更新页面代码

先在正常开发分支完成并提交页面改动，再将已准备好的代码合入部署分支。以下仍在独立部署目录执行：

```powershell
git status --short
# 保持部署目录干净后，合入本机 main 中已提交的代码
git merge main
python -B -m unittest discover -s tests -v
node --check app/app.js
python -B app/build_static.py
git push origin codex/static-deploy
```

若代码来自另一台电脑，先 `git fetch origin`，再合入对应远程分支。解决冲突时保留静态模式和发布白名单。不要将其他会话未完成的修改提交进去。

也可以直接在独立部署目录修改静态部署文件；检查后只 `git add` 本次明确修改的文件，提交到 `codex/static-deploy` 并 push。

## 构建和发布边界

- `data/catalog/`：完整本地官方档案，继续忽略，不直接提交或发布。
- `data/public-catalog/`：经过白名单投影的公开快照，提交到部署分支。
- `dist/`：只包含页面、程序、使用说明、公开快照、当前配方 CSV 和发布清单；不提交。
- 工作流只上传 `dist/`，不会上传仓库根目录。
- 构建与测试不请求 Nikon；没有计划任务、数据库或额外部署令牌。
- `photos/`、`local/`、备份、个人记录、日志、运行配置和本地 Excel 均不进入公开快照。
- 样片和头像使用已下载文件，JSON 不含官方临时图片签名；保留作者、公开参数和官网来源。
- 不认识的字段、重复 ID、路径越界、签名链接、图片哈希变化及额外文件会使构建失败，原已发布站点继续可用。

每次构建生成 `dist/publish-manifest.json`，记录每个发布文件的路径、大小和 SHA256（清单本身除外）。初始快照含 144 个配方、144 张官方样片和 68 张作者头像。

本地 Excel 是独立旧导出，官方同步本就不重建它。网页版提供根据当前公开 JSON 生成的 CSV，不复制未经审计的本机 Excel。

## 首次启用和其他电脑

GitHub 仓库 Settings → Pages → Build and deployment → Source 选择 **GitHub Actions**。此设置只需启用一次。工作流使用 GitHub 自带的短期 `GITHUB_TOKEN` 和 OIDC 权限，不需要添加个人 Token。

在其他电脑检出部署分支后，直接执行 `python -B app/build_static.py` 即可离线构建，Python 3.11+，构建不需要额外 Python 包。仅在导出本地官方档案或运行本地完整版回归测试时安装 `requirements.txt`。

如本机命令行因已经失效的代理无法连接 GitHub，可以仅在当前 PowerShell 进程清除代理环境变量，并临时绕过 Git 代理；不需要改变全局配置：

```powershell
foreach ($proxyName in @('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy')) {
    [Environment]::SetEnvironmentVariable($proxyName,$null,'Process')
}
git -c http.proxy= -c https.proxy= push origin codex/static-deploy
```

## 失败和回退

构建或部署失败时先查看 Actions 日志；网站继续使用上一次成功部署。需要撤回已发布变更时，在部署分支用 `git revert <提交号>` 生成回退提交并 push，避免重写共享历史。

若构建提示输出目录含额外文件，请先检查所列路径，不要把额外文件加入白名单。确认是本地构建残留后，可以删除当前部署目录内的 `dist/` 再重新构建。

## 验证范围

自动测试覆盖公开字段投影、私人文件排除、路径边界、样片和头像哈希、额外文件拒绝、离线构建和 CSV 公式防护。原本的本地同步、回滚、照片和 HTTP 接口测试继续保留。

手机布局和交互使用浏览器验证。实际 iPhone Safari 的网络访问与系统图片保存菜单仍需在手机上确认。
