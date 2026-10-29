# GitHub Pages 部署与更新

手机访问：<https://dddian-phil.github.io/Nikon-Recipe-Library/>

这是公开方案、官方样片与我的实拍的静态站。电脑无需保持开机。网页沿用本地界面，展示实拍、关联结果和本地发布的收藏状态；收藏、喜欢、手动关联、记录备份、扫描照片和访问原图在本地完成。浏览器只记住筛选条件和图片布局，不保存另一份可修改的个人记录。

## 一键同步照片与本地状态

在本地资料库页面点击 **同步到 GitHub 网页**，页面显示准备、生成图片、检查、上传、发布和核对进度；刷新页面仍能读取进度，完成后可点击「打开网页」。它同步本地已有的官方方案、我的实拍、收藏与关联，不主动获取新的 Nikon 方案。只更新实拍直接点击此按钮即可；要更新官方方案，先完成 **同步云创**，再发布。

网页发布和官方同步不能同时运行；重复点击不会启动第二个任务。启动服务、刷新页面和照片自动扫描都不会自动发布。状态与详细日志保存在 `local/website-sync.json` 和 `local/website-sync.log`，不会公开。独立程序入口 `sync-website.cmd` 继续保留为备用方式，与页面共用进程锁。程序依次执行：

1. 请求正在运行的本地服务刷新照片；未运行时执行一次本地离线扫描。
2. 导出当前官方目录，并从本地 JPG 重新生成最长边 480 像素的缩略图和 1280 像素的展示图。Nikon JPG 中的 MPO 容器只取主图。展示副本转为 sRGB，并移除 EXIF、GPS、序列号、内嵌缩略图和其他元数据；网页拍摄参数只包含明确允许的字段。
3. 构建并运行测试，检查图片尺寸、内容哈希、引用关系和发布文件范围。
4. 只暂存 `data/public-catalog/` 和 `data/public-photos/`，有变化才生成 Git 提交，推送 `codex/static-deploy`。
5. 等待该提交的 Pages 工作流完成，读取线上 `publish-manifest.json` 核对完整 Git SHA；核对成功才报告发布完成。

需先安装并登录 GitHub CLI（`gh auth login`），且已完成本功能代码的首次提交。部署目录存在未提交的代码、其他文件或已暂存改动时，会停止，避免把其他工作一起发布；原项目中尚未提交的本地界面修改不会进入此次数据同步。程序不切换原目录分支、不强制推送。修图后的展示图地址包含内容哈希，避免旧缓存；删图后的当前快照会移除对应展示图。此前已公开的 Git 历史副本仍可能存在，删除当前图片不等于撤回历史公开内容。

仅准备和检查本地预览、不提交或推送：

```powershell
powershell -NoProfile -File .\app\sync-website.ps1 -PrepareOnly
```

部署分支在其他路径时，直接运行 `python -B app/publish_site.py --workspace <原项目绝对路径>`。只更新官方方案也可以使用下方原有导出命令；只新增或修图无需联系 Nikon。

网页公开展示图可被保存或截图，但不会发布原始 JPG、原始文件路径或完整个人记录文件。当前不自动添加署名或水印。

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
- `data/public-photos/`：仅含重新编码的缩略图、展示图与只读照片目录，提交到部署分支。收藏仅保留公开方案 ID 与照片喜欢状态，关联只发布最终结果，不发布原始记录文件。
- `dist/`：只包含页面、程序、使用说明、两种公开快照、当前配方 CSV 和发布清单；不提交。
- 工作流只上传 `dist/`，不会上传仓库根目录。
- 构建与测试不请求 Nikon；没有计划任务、数据库或额外部署令牌。
- 原始 `photos/`、`local/`、备份、完整个人记录、日志、运行配置和本地 Excel 均不复制到公开快照。
- 样片和头像使用已下载文件，JSON 不含官方临时图片签名；保留作者、公开参数和官网来源。
- 不认识的字段、重复 ID、路径越界、签名链接、图片哈希变化及额外文件会使构建失败，原已发布站点继续可用。

每次构建生成 `dist/publish-manifest.json`，记录 Git 发布版本、照片数量及每个发布文件的路径、大小和 SHA256（清单本身除外）。网页页脚显示相同版本的前 12 位。

本地 Excel 是独立旧导出，官方同步本就不重建它。网页版提供根据当前公开 JSON 生成的 CSV，不复制未经审计的本机 Excel。

## 首次启用和其他电脑

GitHub 仓库 Settings → Pages → Build and deployment → Source 选择 **GitHub Actions**。此设置只需启用一次。工作流使用 GitHub 自带的短期 `GITHUB_TOKEN` 和 OIDC 权限，不需要添加个人 Token。

在其他电脑检出部署分支后，使用 Python 3.11+ 安装 `requirements.txt`，再执行 `python -B app/build_static.py` 即可从已提交快照离线构建。Pillow 用于校验公开照片的真实尺寸与元数据；普通构建不会读取本地照片或个人记录。

一键同步会检测 Git 配置中未运行的本机代理端口，仅为此次推送绕过它，不修改全局设置或其他代理。手动推送若仍受代理环境变量影响，可以仅在当前 PowerShell 进程清除代理环境变量，并临时绕过 Git 代理：

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

自动测试覆盖公开字段投影、原图与私人字段排除、EXIF 移除、图片尺寸与哈希、修图和删图、路径边界、额外文件拒绝、离线构建、CSV 公式防护，以及 Git 分支和提交范围检查。原本的本地同步、回滚、照片和 HTTP 接口测试继续保留。

手机布局和交互使用浏览器验证。实际 iPhone Safari 的网络访问与系统图片保存菜单仍需在手机上确认。
