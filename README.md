# C3-Mass 火箭运力工作台

查询自己设计的 KSP/RSS 火箭向不同轨道能运送多少载荷，并比较型号能力。目前提供游戏火箭导入、质量和发动机来源核对、实测基准校准、C3 曲线与轨道任务分析。

后续按 [产品路线图](docs/PRODUCT_ROADMAP.md) 建立型号覆盖缺口、MechJeb 自动试飞优化及可重放发射程序；这些功能尚未实现。本机环境见 [游戏安装分析](docs/ksp/KSP_INSTALLATION_AUDIT_20261021.md)。

## 安装与启动

已验证环境为 Windows、Python 3.12。前端静态产物随源码提供，日常使用无需 Node.js。

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run.py
```

也可双击 `启动C3-Mass.bat`，访问 http://localhost:8765。端口可用 `python run.py 8876` 指定。首页与 `/workspace/` 提供同一个 React 工作台；旧版界面已移除。

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
.\.venv\Scripts\python.exe -m tools.plot_capacity rockets/neutron_i_2630.yaml --ksp-root "D:\SteamLibrary\steamapps\common\Kerbal Space Program" --output output/my_curve
```

测试使用隔离数据库、YAML 副本和临时输出。审计只读，不自动覆盖数据。绘图生成离线 HTML、CSV 和来源记录，不初始化正式数据库。更多命令见 [工具说明](tools/README.md)。

## 数据备份与恢复

先停止所有数据写入程序，使数据库和 YAML 处于同一业务时点。

```powershell
.\.venv\Scripts\python.exe -m tools.data backup E:\Backups\c3-snapshot
.\.venv\Scripts\python.exe -m tools.data --root E:\Backups\c3-snapshot restore E:\Recovered\c3-snapshot
.\.venv\Scripts\python.exe -m tools.data --root E:\Recovered\c3-snapshot audit
```

目标目录必须不存在。备份核验 SHA-256，恢复到新目录；正式数据库、火箭设计、原始 craft 和受控实验不能当作缓存清理。SQLite 与 YAML 的跨介质断电原子性仍有限。

## 目录

```text
c3_mass/        计算、发动机解析与任务模型
server/         Flask API、存储、游戏导入和质量证据
frontend/       React 源码、统一浏览器检查与交付用 dist/
game_plugin/    编辑器导出、被动遥测和隔离回放插件源码
migrations/     按顺序执行的数据库迁移
tools/          数据审计、绘图、game/、research/、release/、dev/
tests/          Python 回归测试与合成 fixtures/
docs/           当前路线、models/、ksp/、validation/ 与证据清单
engines/        仍用于首启和计算的发动机样例
rockets/        YAML 配置及首启恢复输入
VAB/            用于来源核对和测试的原始 craft 样例
experiments/    仍被质量证据门控使用的原始实验
output/         生成产物与本地验证记录（忽略）
backups/        本地数据备份（忽略）
```

数据库 `c3_plot.db` 是运行时主数据，`rockets/*.yaml` 是写穿备份和恢复输入。依赖环境、缓存、凭据和生成产物不提交。数据库迁移、历史输入和原始实验仍有实际用途，完整保留。

[文档导航](docs/README.md) · [前端开发](frontend/README.md) · [整理记录](docs/MAINTENANCE.md) · [变更记录](CHANGELOG.md)

公开仓库：[easonyujiang/c3_plot](https://github.com/easonyujiang/c3_plot)。旧版本可通过 Git 历史与 `v0.1.0` 标签查阅。
