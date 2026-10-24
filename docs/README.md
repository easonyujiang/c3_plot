# 文档导航

[项目使用说明](../README.md) · [数据策略](DATA_POLICY.md) · [技术栈与基座](TECH_STACK.md) · [产品路线图](PRODUCT_ROADMAP.md) · [目录与维护](MAINTENANCE.md) · [维护工具](../tools/README.md)

## 当前证据与验证

- [基线飞行 20261024F：1.000 t / 220 km / 20°](evidence/C3Baseline20261024F/README.md)
- [基线飞行 20261024G：2.500 t / 220 km / 20°](evidence/C3Baseline20261024G/README.md)
- [当前验证基线与证据边界](validation/BASELINE.md)
- [本机 KSP/模组与 MechJeb 接口分析](ksp/KSP_INSTALLATION_AUDIT_20261021.md) · [安装哈希清单](evidence/ksp_installation_20261021.json)

## 游戏与数据集成

- [VAB 导入设计](ksp/VAB_IMPORT_DESIGN.md)、[批量导入 API](ksp/BATCH_IMPORT.md)
- [安装索引](ksp/KSP_INSTALL_INDEX.md)、[部件匹配](ksp/KSP_PART_MATCHING.md)
- [质量证据](ksp/KSP_MASS_EVIDENCE.md)、[整箭对账](ksp/KSP_CRAFT_MASS_RECONCILIATION.md)、[模块语义](ksp/KSP_MODULE_MASS_SEMANTICS.md)
- [源验证缓存与导入预览性能](ksp/KSP_SOURCE_VERIFICATION_CACHE.md)
- [火箭 Profile 与参数目录](ksp/ROCKET_PROFILE.md)
- [RealChute 授权范围](ksp/KSP_REALCHUTE_SETTLED_MASS_ENABLEMENT.md)（生产门控仍按固定指纹 fail-closed；原始实验数据已移出工作树）

## 计算模型与 API

- [轨道运力模型](models/ORBIT_CAPACITY_MODEL.md)、[任务数据契约](models/MISSION_SIMULATION_DATA_CONTRACT.md)
- [上升仿真内核 T0/T1](models/ASCENT_SIMULATION.md)
- [燃烧原语](models/MISSION_BURN_PRIMITIVE.md)、[序列求解](models/MISSION_SEQUENCE_SOLVER.md)、[多级求解](models/MISSION_MULTISTAGE_SOLVER.md)
- [输入适配](models/MISSION_INPUT_ADAPTERS.md)、[停泊回放](models/MISSION_PARKING_REPLAY.md)、[级段规划](models/MISSION_STAGE_PLANNER.md)
- [载荷可行性](models/MISSION_PAYLOAD_FEASIBILITY.md)、[容量搜索](models/MISSION_CAPACITY_SEARCH.md)
- [统一分析服务](models/MISSION_ANALYSIS_SERVICE.md)、[任务查询 API](models/MISSION_QUERY_API.md)

这些说明对应当前运行的模型和证据门控。文中出现的 Neutron / `0.502 t` 等数值是数据清零前的回归示例，不是当前火箭库结果。产品执行顺序统一由 [产品路线图](PRODUCT_ROADMAP.md) 决定。

## 历史归档

- [归档说明与目录](archive/README.md)
- [旧产品路线图 2026-10-21](archive/PRODUCT_ROADMAP_20261021.md)
- [技术基座验证 2026-10-21](archive/FOUNDATION_20261021.md)
- [受控质量实验](archive/KSP_CONTROLLED_MASS_EXPERIMENT.md)、[实验结论](archive/KSP_CONTROLLED_MASS_EXPERIMENT_RESULT.md)
- [质量残差](archive/KSP_MASS_RESIDUAL_FORENSICS.md)、[数值精度](archive/KSP_NUMERIC_PRECISION_FORENSICS.md)

归档文档不是当前产品方向、当前证据或生产授权；保留用于追溯方法和边界。
