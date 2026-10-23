# Nikon Recipe Library

本地浏览尼康云创色彩方案、作者头像、官方样片与拍摄参数，并将自己的 JPG / NEF 照片关联到使用的方案。

支持按作者和器材筛选、方案收藏、照片喜欢、自动增量扫描、手动关联及个人记录备份。界面使用中文，文件和目录使用英文。

## 日常使用

1. 双击 `open-library.cmd` 打开资料库。
2. 将照片放进 `photos/`，也可以建立子文件夹；服务运行时每 8 秒自动检查。
3. 需要立即刷新时，点击页面上的「更新照片库」，或双击 `update-photos.cmd`。

完整使用说明请打开根目录的 `guide.html`，也可以阅读 [文档源码](docs/guide.md)。已有的本地官方素材下载完成后，日常浏览无需联网。

## 首次安装

需要 Python 3.11 或更新版本。以下命令在项目根目录执行：

```powershell
python -m pip install -r requirements.txt
python app/collect_nikon.py
python app/build_viewer.py
.\open-library.cmd
```

采集器读取尼康中国站公开接口，无需账户、Cookie 或 Token。它只下载公开目录及官方展示图；公开样片中的相机、镜头是拍摄器材，不表示方案的兼容性限制。

如需指定 Python 路径或端口，将 `app/runtime.example.json` 复制为 `local/runtime.json` 后编辑。此机器原有的运行配置已保存在该位置。

也可以直接运行：

```powershell
python app/library_server.py --port 8765
```

然后访问 `http://127.0.0.1:8765/`。服务仅监听本机回环地址。

## 项目结构

| 路径 | 用途 |
|---|---|
| `open-library.cmd` | 打开资料库 |
| `update-photos.cmd` | 立即更新照片索引 |
| `guide.html` | 可直接阅读的使用说明 |
| `photos/` | 自己的原始照片，保留原文件名 |
| `app/` | 前端、照片解析、采集器及本地服务 |
| `data/catalog/` | 下载的官方方案、头像、样片、JSON 和 CSV |
| `local/` | 个人记录、照片索引、预览、运行配置和历史检查 |
| `docs/` | 使用文档源码 |
| `recipes.xlsx` | 本机已有的 Excel 总表 |

照片和 `local/` 全部内容均由 `.gitignore` 排除。下载的官方素材、Excel 导出及生成的 `app/viewer.html` 也不提交；新电脑按首次安装步骤重新生成资料库。复制现有照片库到其他电脑时，请另外保留 `photos/` 和 `local/`。

## 维护

```powershell
# 从已有本地素材重新生成目录，保持离线
python app/collect_nikon.py --offline
python app/build_viewer.py

# 修改说明文档后重新生成阅读页
python app/build_guide.py
```

`app/build_workbook.mjs` 保留原有 Excel 导出工具，需要额外配置 `@oai/artifact-tool` Node 模块；日常浏览和照片管理不需要它。当前 `recipes.xlsx` 已同步更新文件路径。

本项目为个人本地整理工具，与尼康官方无隶属关系。原始照片不被程序修改。自动关联依据照片中的优化校准名称，不等于已核对全部调色参数；名称缺失、截断或有歧义时可手动选择。
