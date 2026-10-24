# C3-Mass 火箭运力工作台

查询自己设计的 KSP/RSS 火箭向不同轨道能运送多少载荷，并比较型号能力。目前提供游戏火箭导入、质量和发动机来源核对、实测基准校准、C3 曲线与轨道任务分析。

后续按 [产品路线图](docs/PRODUCT_ROADMAP.md) 建立型号覆盖缺口、MechJeb 自动试飞优化及可重放发射程序；这些功能尚未实现。本机环境见 [游戏安装分析](docs/ksp/KSP_INSTALLATION_AUDIT_20261021.md)。

## 安装与启动

运行基座：Python 3.12–3.14 + Flask / Waitress + SQLite；界面采用 React 19 + TypeScript + Vite + TanStack Query，图表与资源均在本地提供。详见 [技术栈与基座](docs/TECH_STACK.md)。

Linux / WSL：

```bash
./scripts/setup
./c3 --doctor
./c3 --open
```

Windows 原生：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/setup.ps1
.venv/Scripts/python.exe -m c3_mass --doctor
.venv/Scripts/python.exe -m c3_mass --open
```

首次安装和前端开发需要 Node.js 20.19+ 或 22.12+。构建后的日常使用无需 Node.js，也不需要下载测试浏览器。服务默认访问 http://127.0.0.1:8765，端口可通过 `--port 8876` 或 `.env` 中的 `C3_MASS_PORT` 指定。首页与 `/workspace/` 是同一入口。

复制 `.env.example` 为 `.env`，按实际安装位置设置 `C3_MASS_KSP_ROOT`。本机 `启动C3-Mass.bat` 使用 WSL 启动；Windows 原生请使用上述命令。

## 使用流程

1. 安装或更新游戏导出插件后，重新启动 KSP，在 VAB 保存火箭并等待约两秒。
2. 打开工作台“工具箱 → 游戏火箭导入”，选择 craft 并核对自动识别信息。
3. 为同一构型填写三个实测基准：220 km 圆轨道、220 × 35786 km 转移轨道、地球逃逸 C3=0；也可展开自定义基准。
4. 保存火箭档案，查看曲线；在任务工具中输入载荷、近远地点和倾角，比较火箭库。

净载荷以模板外额外携带质量为基准，模板设备不要重复计入。火箭构型、燃料、发动机或模组改变后，应重新保存、核对并校准。缺少匹配快照、来源过期或分级有歧义时，需要复核。

当前模型采用实测校准与理想燃烧序列。有限推力、FAR 气动、级寿命、蒸发和 Principia 多体轨迹尚未被完整仿真；外推结果不能视为已验证运力。整流罩选项只扣减可用载荷，不模拟抛弃。历史证据及其边界见 [验证基线](docs/validation/BASELINE.md)。

## 命令行与检查

从项目根目录运行工具，统一使用 `python -m`：

```powershell
.\.venv\Scripts\python.exe run_tests.py
.\.venv\Scripts\python.exe -m tools.data audit
.\.venv\Scripts\python.exe -m tools.plot_capacity rockets/<已保存的火箭>.yaml --ksp-root "D:\SteamLibrary\steamapps\common\Kerbal Space Program" --output output/my_curve
```

测试使用隔离数据库、YAML 副本和临时输出。审计只读，不自动覆盖数据。绘图生成离线 HTML、CSV 和来源记录，不初始化正式数据库。更多命令见 [工具说明](tools/README.md)。

## 数据与测试

项目已清零：不附带 VAB、发动机副本、火箭样例、实验记录、计算结果和备份。首次启动只建立空数据库结构，不灌入火箭、发动机或任务样例。

真实输入统一来自 `.env` 的 `C3_MASS_KSP_ROOT`：火箭从 `saves/*/Ships/VAB/*.craft` 即读，部件与发动机从 `GameData` 读取。游戏目录只读，不复制为项目固定样例。游戏目录缺失时给出配置提示。

```bash
.venv/bin/python run_tests.py
```

测试使用临时数据库和临时输出，结束后移除。真实游戏数据的动态检查见 `tests/test_live_game_data.py`；旧固定数据集的数值回归已标记停用，不会把旧标定冒充当前游戏的结果。算法单元测试仍使用代码内的最小输入；`.NET` 指令规范表不是游戏数据，予以保留。

后续手动导入保存的业务档案会写入本地数据库与 `rockets/`；不会在启动时自动导入目录内容。已有备份、恢复工具仍可用于未来新建数据，见 [工具说明](tools/README.md)。

[文档导航](docs/README.md) · [前端开发](frontend/README.md) · [整理记录](docs/MAINTENANCE.md) · [变更记录](CHANGELOG.md)

公开仓库：[easonyujiang/c3_plot](https://github.com/easonyujiang/c3_plot)。旧版本可通过 Git 历史与 `v0.1.0` 标签查阅。
