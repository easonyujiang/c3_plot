# 文档导航

[项目使用说明](../README.md) · [数据清零与游戏源](DATA_POLICY.md) · [技术栈与基座](TECH_STACK.md) · [产品路线图](PRODUCT_ROADMAP.md) · [目录与清理记录](MAINTENANCE.md) · [维护工具](../tools/README.md)

## 游戏与验证

- [本机 KSP/模组与 MechJeb 接口分析](ksp/KSP_INSTALLATION_AUDIT_20261021.md)
- [当前验证基线与精度边界](validation/BASELINE.md)
- [VAB 导入设计](ksp/VAB_IMPORT_DESIGN.md)、[批量导入 API](ksp/BATCH_IMPORT.md)
- [安装索引](ksp/KSP_INSTALL_INDEX.md)、[部件匹配](ksp/KSP_PART_MATCHING.md)
- [质量证据](ksp/KSP_MASS_EVIDENCE.md)、[整箭对账](ksp/KSP_CRAFT_MASS_RECONCILIATION.md)
- [质量残差](ksp/KSP_MASS_RESIDUAL_FORENSICS.md)、[模块语义](ksp/KSP_MODULE_MASS_SEMANTICS.md)、[数值精度](ksp/KSP_NUMERIC_PRECISION_FORENSICS.md)
- [受控实验](ksp/KSP_CONTROLLED_MASS_EXPERIMENT.md)、[实验结论](ksp/KSP_CONTROLLED_MASS_EXPERIMENT_RESULT.md)、[RealChute 授权范围](ksp/KSP_REALCHUTE_SETTLED_MASS_ENABLEMENT.md)

## 计算模型与 API

- [轨道运力模型](models/ORBIT_CAPACITY_MODEL.md)、[任务数据契约](models/MISSION_SIMULATION_DATA_CONTRACT.md)
- [燃烧原语](models/MISSION_BURN_PRIMITIVE.md)、[序列求解](models/MISSION_SEQUENCE_SOLVER.md)、[多级求解](models/MISSION_MULTISTAGE_SOLVER.md)
- [输入适配](models/MISSION_INPUT_ADAPTERS.md)、[停泊回放](models/MISSION_PARKING_REPLAY.md)、[级段规划](models/MISSION_STAGE_PLANNER.md)
- [载荷可行性](models/MISSION_PAYLOAD_FEASIBILITY.md)、[容量搜索](models/MISSION_CAPACITY_SEARCH.md)
- [统一分析服务](models/MISSION_ANALYSIS_SERVICE.md)、[任务查询 API](models/MISSION_QUERY_API.md)

这些技术说明对应仍在运行的模型和证据门控。阶段名称描述其实现来源，当前产品执行顺序统一由路线图决定；不再保留重复的旧计划和过程复盘。
