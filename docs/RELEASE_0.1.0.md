# C3-Mass 0.1.0

2026-10-11，本地 Windows 发布。版本对应 Git 标签 `v0.1.0`。

## 安装与使用

解压 Windows 发布包到独立目录，安装 Python 3.12，在该目录执行：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.\.venv\Scripts\python.exe run.py
```

后续双击 `启动C3-Mass.bat` 即可。界面已编译，使用时不需要 Node.js。
发布包的 `GameData/C3MassTelemetry` 复制到 KSP 的 `GameData` 内；退出游戏后安装，重新启动，在 VAB 正常保存火箭，等待约两秒。
若已有同名插件，先备份并核对版本。插件源码和构建工具保留在项目中；不包含游戏本体程序集或内部验收插件。

工具箱会列出游戏 craft。导入后核对自动读取信息，填写三个基准运力，保存绘图。
任务查询支持自定义近远地点、倾角和多次点火，整流罩选项只扣减可用载荷。
已有用户升级前先运行 `data_tools.py backup`；发布包不包含原用户数据库，不覆盖已有数据。

## 支持范围

- 自动识别已核验的串联分级，依据当前 craft、ModuleManager 缓存、插件运行时质量和资源数据；径向并联、交叉供油或发动机配置歧义需人工复核。
- 使用三个实测基准校准理想火箭方程和轨道速度预算；支持二次、三次点火能力与约束检查。
- 倾角进入速度预算；物理 C3 与含额外速度需求的等效 C3 分别显示。
- FAR、Principia、MechJeb 和修正模组对实际飞行的影响依赖同一游戏环境下的实测基准。没有完整重演有限推力、长时间滑行、蒸发和摄动。
- 当前界面采用 220 km 停泊轨道及 19.81° 发射参考纬度。历史 Neutron 基准来源未完整记录，应重新实测后提高可信度。

## 验收依据

935 项测试通过，0 失败、0 错误、0 跳过。覆盖质量来源、资源、导入、存储、轨道与点火约束；测试入口隔离数据库、火箭文件及输出。
前端生产构建包含类型检查，浏览器验收涵盖 390/768/1440 像素窗口、悬停、缩放、平移、复位与整流罩切换。
发布包另行解压，使用全新数据库验证首次启动、静态资源和任务查询。最终记录位于本地 `output/release_20261011`。

700 kg → 400 × 40000 km、89°：历史 Neutron I 2630 模型估算约 1198 kg；额外计入 200 kg 整流罩后净运力约 998 kg。
独立火箭方程与轨道公式复算差约 0.37 kg；这是计算一致性验证，尚未完成该任务实际游戏飞行验收。
详见 [独立复算](INDEPENDENT_CHECK_20261011.md) 与 [工作流验收](WORKFLOW_ACCEPTANCE_20261011.md)。

## 构建与版本控制

前端开发使用 `npm ci`、`npm run build`；最终静态产物纳入 Git。
运行 `python run_tests.py` 完成后端回归。通过后提交、打标签，再执行：

```powershell
.\.venv\Scripts\python.exe tools/package_release.py --plugin output/curve_accuracy_20261010/plugin/C3MassTelemetry.dll
```

打包工具仅接受干净的 Git 工作区，生成 Windows 包、源码包和逐文件 SHA-256 清单。
生成物、数据库、缓存、虚拟环境、node_modules 和备份不进入 Git。
本地样例中含原游戏及模组配置，保留其来源；项目没有替这些文件重新授予许可证，公开分发前需按各来源许可整理。

## 本次工作区整理

原数据库与火箭配置已备份并通过只读审计。清理 6 个缓存/临时目录，11 份大型历史日志无损压缩并核验原始 SHA-256，释放约 377 MiB。
日志可从相邻 `.log.gz` 恢复，原始路径、压缩前后大小和校验值保存在 `output/release_20261011/cleanup-manifest.json`。
旧 `.pytest_cache` 的 Windows 访问权限异常，清理和权限修复均被拒绝；目录保留并忽略，不进入 Git 或发布包。
