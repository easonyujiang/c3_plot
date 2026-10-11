# C3-Mass 运力数据库

这是面向 KSP/RSS 火箭的本地运力管理、C3/载荷曲线和地球轨道任务分析工具。

## 安装与启动

已验证环境：Windows、Python 3.12。项目可搬迁，路径根据源码位置解析。

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.\.venv\Scripts\python.exe run.py
```

也可以双击 `启动C3-Mass.bat`。浏览器地址为 http://localhost:8765。
端口可用 `python run.py 8876` 指定。运行时依赖快照在 requirements-lock.txt，
开发测试依赖在 requirements-dev.txt。不要复制其他机器的 .venv，重新创建即可。

浏览器默认打开新版工作台，分为运力概览、只读火箭档案、工具箱。修改火箭须进入独立修改界面，取消不写入。旧界面保留在 `/legacy/`，构建说明见 [前端说明](frontend/README.md)。

## 游戏保存到运力曲线

本机的编辑器导出插件已安装。首次使用请重新启动 KSP，让插件加载，然后在 VAB 正常保存火箭，等约两秒。

1. 打开工具箱 → 游戏火箭导入，列表自动读取当前安装各存档的 VAB craft；找到火箭并点击导入。
2. 软件核对 craft 版本与当前 mod，自动填写分级质量、已配置发动机、油箱容积和点火限制。
3. 默认只填写三个任务的实测运力：220 km 圆轨道、220 × 35786 km 转移轨道、地球逃逸 C3=0；已有其他基准点可展开自定义轨道与剩余速度。
4. 保存后查看火箭档案与曲线；在工具箱填写卫星质量、近远地点和倾角，筛选整个火箭库。

基准运力指该 craft 模板之外额外携带的质量，模板中已有设备不要重复填写。设计、燃料配比、发动机配置或 mod 发生变化后，请在游戏重新保存并重新校准。缺少匹配快照、来源过期或分级有歧义时，软件明确提示，避免把静态配置猜测值当作实际质量。

完整实机与浏览器验收、700 kg 案例和识别范围见 [工作流验收](docs/WORKFLOW_ACCEPTANCE_20261011.md)。

## 直接绘制当前游戏安装对应的曲线

```powershell
.\.venv\Scripts\python.exe plot_capacity.py rockets/neutron_i_2630.yaml --ksp-root "D:\SteamLibrary\steamapps\common\Kerbal Space Program" --output output/my_curve
```

打开输出目录的 `C3_mass.html` 即可交互查看，离线可用；同时生成 CSV、计算结果和来源指纹。
发动机参数读取该安装的最终 ModuleManager 配置，质量、发动机数量及校准仍取自 YAML。
蓝线表示已有校准载荷范围内的模型，橙色虚线为外推；现有校准原始记录未回填，不能据此声称精度已验证。
当前不支持自动选择多模式发动机或 RealFuels 可切换配置，遇到歧义会中止而非猜测。

游戏内被动采样与本次精度修复见 [推进记录](docs/CURVE_ACCURACY_20261010.md)。
已完成独立存档副本的在轨采样验收，实际数据与范围见 [游戏内验收记录](docs/GAME_VALIDATION_20261010.md)。

## 数据与模型边界

mod 动态质量审查的历史过程见 [自动导入推进记录](docs/AUTOMATIC_IMPORT_20261010.md)，最新结果以上述工作流验收为准。性能曲线支持额外整流罩质量扣减，只改变净载荷，不模拟抛弃或修改基准点。

- SQLite（c3_plot.db）是运行时主数据；rockets/*.yaml 是配置备份和首启恢复输入。
- output 是可重新生成的产物；VAB、experiments 是应保留的来源与实验证据。
- SQL 和 YAML 写入有异常回滚，但跨文件与数据库的断电原子性仍有限；定期备份。
- 当前任务分析是经验校准与理想燃烧序列模型，不代表完整上升段或有限推力仿真。
- 有限推力、级寿命、并联详细任务燃烧等限制仍有效；测试通过不能证明物理精度。
- 新工作流对支持的串联构型自动填写质量与发动机，用户提供三个实测基准；旧 VAB 草稿导入保留人工确认方式。
- RealChute 只支持已有实验授权的窄域，指纹或适用范围不符时保持 unresolved。
- 旧数据没有来源记录时显示来源未回填；不能据此宣称经过来源验证。

## 检查与测试

所有测试运行前，将临时目录和数据库指向副本，避免 create_app 初始化写入正式数据库。

```powershell
.\.venv\Scripts\python.exe run_tests.py
.\.venv\Scripts\python.exe data_tools.py audit
```

审计区分 matching、different、sql_only、yaml_only、invalid，只读，不自动选择覆盖方向。
比较忽略键顺序及衍生显示值，保留质量、发动机组、校准和任务目标的语义。
迁移失败会中止启动；若提示同级文件夹重名，应先备份、明确重命名方案，不删除数据。

## 备份与恢复

先停止服务和其他写入程序，让 SQLite 与 YAML 处在同一个业务时点。

```powershell
.\.venv\Scripts\python.exe data_tools.py backup E:\Backups\c3-20261009
.\.venv\Scripts\python.exe data_tools.py --root E:\Backups\c3-20261009 restore E:\Recovered\c3-20261009
.\.venv\Scripts\python.exe data_tools.py --root E:\Recovered\c3-20261009 audit
```

目的目录必须不存在。备份包含 SQLite、YAML、发动机定义、VAB 和实验证据，
恢复前核验 SHA-256，恢复后检查完整性与配置一致性。恢复数据不会覆盖当前项目；
使用恢复数据时，应另行复制源码并安装依赖。正式数据修复须明确方向并先备份。

## 文件分类与维护

源码、tests、migrations、docs、依赖文件适合纳入版本控制。
数据库、YAML、VAB、实验证据保留在本地，是否提交由使用者根据大小和用途决定。
.venv、缓存、SQLite 临时文件、生成输出和 backups 已列入忽略规则。
本地 Git 使用 main 分支与 v0.1.0 发布标签；未配置远程仓库。数据库、依赖环境、备份和生成输出不提交。
保留的 engines、VAB、rockets 和 experiments 是本地历史样例与验证材料，不代表当前游戏运行时数据。

当前版本与安装说明见 [0.1.0 发布说明](docs/RELEASE_0.1.0.md)，历史修复和规划保留用于追溯。
