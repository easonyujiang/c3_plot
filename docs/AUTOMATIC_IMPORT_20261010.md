# 自动识别与三点运力工作流

> 本文保留 2026-10-10 的历史推进状态。自动填写、有限点火次数、来源持久化和完整操作验收现已完成，最新结果见 [2026-10-11 工作流验收](WORKFLOW_ACCEPTANCE_20261011.md)。下文待完成项不代表当前状态。

本轮目标以本文件为准：保存火箭 → 软件发现 craft → 导入并自动填写 → 用户填写三个基准点 → 绘图并查询自定义轨道。继续使用经验校准模型，不开发 kOS 发射器或自动试飞控制器。

## 已完成

- 导入窗口新增游戏存档火箭列表，扫描 saves/<存档>/Ships/VAB；默认安装目录沿用项目设置，可通过 C3_MASS_KSP_ROOT 设置其他安装。窗口打开时每五秒刷新，关闭时停止。
- 列表按修改时间排序，提供名称、文件名、存档和版本 SHA-256。点击导入前重读文件核对指纹，重新保存的文件须刷新后导入。读取对应 loadmeta，不改动来源。
- 新增列表路径约束、修改检测、配对元数据和 API 回归测试；六项通过。
- 图表支持可选的额外整流罩质量：按用户明确选择，净载荷 = 原运力 − 整流罩质量，不模拟抛弃，不重新拟合三个基准点。已计入箭体的质量不再重复扣减。C3 曲线和 Δv 曲线、任务参考线同步；CSV/JSON 和任务查询保持原始运力，界面明确说明。
- 整流罩代数、边界插值、关闭后恢复和原始数据不被修改的检查通过。
- 390 / 1280 像素真实浏览器验证：发现 65 个 craft，0.200 t 扣减令图表对应载荷由 4.000 t 变为 3.800 t，关闭恢复；无页面错误或溢出。现有任务、时间线与总览在 390 / 768 / 1280 像素回归通过。

## 发动机与油箱审查

来源归档在 output/automatic_import_20261010/mass_sources_pinned（旧的失败尝试保留在 mass_sources），记录固定提交与文件 SHA-256；本机 SMURFF 补丁也只读复制并记录指纹。

- RealFuels ModuleEngineConfigs.SetConfiguration 内，质量包含 origMass、当前配置倍率、RF 全局发动机质量倍率和技术等级倍率；另提供质量差修正。不能只采用原始 PART.mass，不能在已结算质量上重复加 massDelta。
- SSTUModularEngineCluster 的质量修正包含集群数量和支架附加质量；集群实现对应一个运行时发动机模块，不能再把其总推力或结算质量重复乘以数量。
- RealFuels ModuleFuelTanks.CalculateMass 根据基础质量、容积、各子油箱单位容积质量与 MassMult 计算，并区分附加质量与替换默认质量。其 totalVolume、volume 与 utilization 含义不同；资源 amount / maxAmount 不能一概当作升数。
- SSTUVolumeContainer 汇总子容器的质量，区分 rawVolume、usableVolume、tankageVolume，并可通过 subtractMass 替换默认质量。
- 本机 SMURFF 修改 PART.mass、B9 tankMass、SUBTYPE.addedMass 和 TankContentSwitcher 等字段；须读取最终补丁与运行时选择，不能只看 mod 原始源配置。
- ProceduralParts 2.8.0.0 的 TankContentSwitcher.GetModuleMass 返回 mass-defaultMass；质量由所选类型的 dryDensity × tankVolume + massConstant 计算，tankVolume 显示单位为 L。ProceduralPart 自己也提供模块质量，不能重复加总。
- B9PartSwitch 2.21.0.4 的 GetModuleMass 返回当前子类型的附加干质量：GetTotalVolume × tankMass + addedMass × VolumeScale；容积还受当前子类型倍率、附加容积和子部件影响。原始 PART 的默认值不能代表当前选择。两个版本均按明确提交归档，避免使用最新 master 冒充安装版本。

## 编辑器质量导出

新建 game_plugin/C3EditorExporter.cs，加入现有自用采样插件的构建：监听用户正常保存，在文件写入和两次质量检查后，导出来源 SHA-256 对应的部件质量、资源、发动机及模块状态。不主动保存、修改或发射火箭。

通过本机 Assembly-CSharp.dll 的只读元数据和 IL 核查：ShipConstruct.GetShipMass 先调用 Part.UpdateMass，再读取 Part.mass 与资源；UpdateMass 按 prefabMass 和模块质量修正结算。导出通过标准编辑器总质量接口取得这个结果，不另猜质量倍率、不二次叠加修正。

已编译并安装，当前 DLL SHA-256 为 0636739F99D784F7DF05156281F72C7A666EB661830DD2F76AD68792AFF21DD1。编辑器实际保存验收与自动填表尚未完成。

已查明之前 ConfigCache 缺失的原因：本项目飞行采样将含 PART 节点的 `.cfg` 存在 GameData，游戏将其当成了额外零件配置，导致 SMURFF 81 个错误。九个自用采样文件已备份并核对 SHA-256，扩展名改为 `.c3snapshot`；新采样写入游戏根目录下的 PluginData/C3MassTelemetry/Flight 与 EditorSnapshots，双重避免配置加载。只改动本项目生成的日志和 DLL，没有删除其他 mod 配置。

受控主菜单启动 106.12 秒完成：ConfigCache 恢复、不再含采样零件 URL，81 个错误消失。保留四个安装自身补丁警告。随后全量 924 项测试全部通过，0 失败、0 跳过。旧采样记录仍保留；此前被污染配置环境下的飞行记录不能单独证明安装物理参数准确。

## 新前端与验收任务

React + TypeScript + Vite 构建，新入口 /workspace/；原界面保留。页面按用户要求分开：

- Dashboard：概览与任务入口。
- Dictionary：只读火箭分级、基准点、曲线。
- 实用工具：游戏火箭导入、自定义近远地点与倾角的任务筛选。
- 独立修改：只有进入 #/edit/<slug> 才可修改、保存；取消不写入。

390 / 768 / 1440 像素浏览器检查通过，覆盖只读档案、取消不写入、编辑保存、导入质量保护、任务筛选，无页面错误或横向溢出。验证使用复制数据库，不修改原数据库。

用户验收例：700 kg 卫星，400 × 40000 km，89°。已接入 elliptical 轨道参数，220 km 停泊后先抬远地点，再于远地点调整近地点；停泊后两次点火，按同一上面级计算包含入轨共三次点火。目标 C3 为 -15.001 km²/s²，额外速度预算约 2960.5 m/s（含发射方向的地球自转差异）。

现有档案筛选出 Neutron I 2630 的模型候选，可用载荷估计约 1198 kg、700 kg 下余量约 498 kg（不额外扣整流罩）。700 kg 低于现有最低基准载荷 1055 kg，界面明确标记外推候选；recommendation_eligible=false，没有把该结果冒充有实测支持的正式推荐。自动识别和有限点火次数仍待完成，最终 goal 未完成。

## 后续必要工作

1. 完成导出插件保存版本匹配、安装指纹、编辑器总质量对账和实际保存验收；自动导入只接受匹配当前 craft 与安装的快照。
2. 从连接及分离关系、燃料流向建立物理级；区分非飞行支架、载荷、不可消耗资源和动力级，不直接按 istg 求质量。
3. 接入游戏中当前配置的发动机性能，持久保存其来源，避免保存后重新计算退回旧 CFG。
4. 接入有限点火次数与当前剩余状态，在入轨之后扣除已使用次数；缺失限制不默认无限重启。
5. 保留三个基准点和可追溯入轨状态；支持自定义近/远地点、倾角和可执行燃烧方案，明确有限推力、级寿命等未验证范围。
6. 通过实际 craft 的导入→填写三个点→绘图→自定义轨道查询完整验收后，才完成 goal。

本轮全量首次检查 924 项：1 项失败、14 项跳过。已定位失败为一项真实安装取证测试在 ConfigCache 缺失时仍断言历史结论；为它补上与其他真实安装测试一致的存在条件，不改生产物理逻辑或放宽质量容差。修复后结果另存 tests-003.txt。缺少缓存时跳过的实机测试仍须在缓存恢复后重跑。
