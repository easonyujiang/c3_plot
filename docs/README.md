# 文档导航

日常安装与启动见 [项目说明](../README.md)。本轮目录整理将原根目录的计算与任务模块迁入 `c3_mass/`；历史文档的旧路径、行号和阶段状态保留用于追溯。

## 当前维护入口

- [后续改进计划](NEXT_STEPS.md)：本轮代码检查后的优先级和验收条件。
- [0.1.0 发布说明](RELEASE_0.1.0.md)：已发布版本的安装、功能与限制。
- [工作流验收](WORKFLOW_ACCEPTANCE_20261011.md)：游戏保存、导入、校准、查询的验收范围。
- [独立复核](INDEPENDENT_CHECK_20261011.md)：独立检查与精度结论的边界。

## 计算与任务模型

- [轨道运力模型](ORBIT_CAPACITY_MODEL.md)
- [任务数据契约](MISSION_SIMULATION_DATA_CONTRACT.md)
- [燃烧原语](MISSION_BURN_PRIMITIVE.md)、[序列求解](MISSION_SEQUENCE_SOLVER.md)、[多级求解](MISSION_MULTISTAGE_SOLVER.md)
- [输入适配](MISSION_INPUT_ADAPTERS.md)、[停泊回放](MISSION_PARKING_REPLAY.md)、[级段规划](MISSION_STAGE_PLANNER.md)
- [载荷可行性](MISSION_PAYLOAD_FEASIBILITY.md)、[容量搜索](MISSION_CAPACITY_SEARCH.md)
- [统一分析服务](MISSION_ANALYSIS_SERVICE.md)、[查询 API](MISSION_QUERY_API.md)

## 游戏集成与质量证据

- [VAB 导入设计](VAB_IMPORT_DESIGN.md)、[安装索引](KSP_INSTALL_INDEX.md)、[部件匹配](KSP_PART_MATCHING.md)
- [质量证据](KSP_MASS_EVIDENCE.md)、[整箭对账](KSP_CRAFT_MASS_RECONCILIATION.md)
- [模块质量语义](KSP_MODULE_MASS_SEMANTICS.md)、[数值精度取证](KSP_NUMERIC_PRECISION_FORENSICS.md)
- [受控质量实验](KSP_CONTROLLED_MASS_EXPERIMENT.md)、[实验结果](KSP_CONTROLLED_MASS_EXPERIMENT_RESULT.md)

## 历史记录

`PROJECT_*`、`FINAL_ROADMAP.md`、`AI_STAGED_IMPLEMENTATION_PLAN.md` 以及带日期的修复、自动导入和游戏验证记录描述各自检查时点，阅读时先核对日期与后续验收。它们不代表当前待办列表；后续工作以本轮 [改进计划](NEXT_STEPS.md) 为入口。
