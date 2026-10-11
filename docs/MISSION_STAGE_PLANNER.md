# 停泊轨道后显式任务级规划器（Post-Parking Stage Planner）

阶段 3F 交付物：`mission_stage_planner.py`（项目根目录，纯 Python）。

## 1. 定位与边界

**输入必须是停泊轨道时的状态**：`replay_serial_ascent_to_parking` 的输出（或与之等价的"已分离前缀 + 仍连接后缀"状态）+ 当前整箭质量 + 任务载荷 + `burn_sequence_to_requirements` 生成的 BurnRequirement（真实燃烧尚未分配执行级）。

规划器输出一份完整、可审计、可直接交给 `simulate_multistage_sequence` 的计划：

- 每个真实燃烧的 `preferred_stage_index`。
- 每次燃烧前的 `separations_before`。
- 规划后的预测级状态与整箭质量。
- 无法规划时的稳定失败原因。

**规划器只负责寻找方案，不替代 `simulate_multistage_sequence` 的最终验证**——成功计划的 `metadata['plan_verified']=False`，只有后续编排层真正执行后才会置为 True。

```
plan_post_parking_sequence(
    rocket_ref,
    stages,            # 从下到上；detached 级必须是列表前缀
    requirements,      # calibration 未分配 + 真实燃烧 preferred 全 None
    vehicle_mass_t,
    payload_t,
    data_quality=DataQuality.EXACT,
    max_search_nodes=10000,
) -> PostParkingPlanResult
```

依赖：标准库 + `mission_simulation.py` + `mission_solver.py`（`execute_burn`、`available_delta_v_m_s`、`MASS_TOLERANCE_T`）。不依赖 compute_orbit_mission / compute_rocket / YAML / Flask / SQLite / 前端 / .craft-VAB / NumPy。

## 2. 候选级、分离与重启规则

- 每个真实燃烧的候选执行级来自**仍 attached 的级**，按 stages 的**从下到上**顺序尝试。
- 使用某个更上面的级前，必须分离列表中它下面所有仍 attached 的级：
  - 分离发生在该燃烧之前。
  - 分离质量 = 该级当前的 `dry_mass_t + propellant_remaining_t`（未使用推进剂也会被丢弃，累计到 `discarded_propellant_t` 并产生 warning）。
  - `separations_before[burn_id]` 按原 stages 顺序记录级索引。
- 不得分离候选执行级；已 detached 的级不得再次分离；不得让上面级点火而下面级继续 attached。
- 分配的执行级位置随任务推进只能保持不变或向上，不能回到已分离的下级。
- 候选级必须能独立完成该次完整燃烧：`ignition_count=0` 是 FIRST（不要求 multi_ignition）；`ignition_count>0` 是 RESTART（必须 `multi_ignition=True`）。
- 必须复用 `execute_burn` 计算和推进状态；不重新实现火箭方程；失败候选（RESTART_UNSUPPORTED / PROPELLANT_INSUFFICIENT）不得提交状态。

## 3. whole_burn_per_stage 限制

本阶段 `whole_burn_per_stage=True`：**每个 BurnRequirement 必须由一台明确的级完整完成**。

禁止：下一级贡献部分 Δv 再由上一级完成同一个 requirement；把一个 requirement 暗中拆成多个 BurnResult；多级同时燃烧；并联燃烧。

如果多级合计推进剂可能足够、但没有任何单一级能完成该次完整燃烧：

- 规划失败，`failure_code=PROPELLANT_INSUFFICIENT`。
- `failure_reason_zh` 说明"完整燃烧不可跨级"的当前模型限制。
- `warnings`/说明提醒：这**不等价于**证明真实火箭绝对不可完成；未来需要单次机动跨级模型。

## 4. 规划策略：最低可达且能够完成燃烧的级（无跨燃烧回溯）

`planning_policy = 'lowest_reachable_capable_stage'`。对每个真实燃烧：

1. 从当前最低 attached 级开始，按 stages 输入顺序检查候选级。
2. 测试某个更上面的候选级时，在**临时副本**中分离它下面全部 attached 级，用分离后的整箭质量调用 `execute_burn`。
3. 选择**第一个能够完整成功执行**该燃烧的候选级并提交：分离事件、preferred_stage_index、燃烧后的级状态、当前整箭质量。
4. 候选失败则继续检查更上面的级；全部失败则用**该燃烧开始前的现场快照**生成失败诊断。
5. **不再回退修改已经成功提交的较早燃烧分配**。候选测试中的临时状态绝不提交到正式结果。

### 4.1 低级优先支配性质（为什么不需要跨燃烧回溯）

在当前模型中（级只能从下向上使用、使用上面级前必须分离所有下面级、一次燃烧必须由单级完成、没有点火次数上限（只有 multi_ignition 布尔）、没有级寿命/滑行时间约束）：

**如果当前最低可达级能够完成本次燃烧，使用它不会降低后续可行性**：

- 上面级推进剂保持更多。
- 上面级 ignition_count 不会提前增加。
- 以后仍可分离当前级并使用上面级。
- 提前分离当前级并改用上面级不可能保留更多未来能力。

因此"较早燃烧分配成功后因后续失败而回退修改较早分配"的跨燃烧回溯在本模型中是多余的；`test_falls_through_to_next_candidate_when_lower_stage_cannot_complete_burn` 只验证"同一燃烧先检查较低级、较低级推进剂不足后尝试更上级"的候选级顺序尝试。**未来加入级寿命、重启次数上限等约束后，可能需要重新引入真正的回溯或动态规划。**

### 4.2 max_search_nodes 语义

- 表示**最大候选级评估次数**，不是指数回溯节点数。
- metadata 中继续使用 `search_nodes` 字段（兼容），文档化含义为 candidate evaluations。
- 超过上限仍返回 `DATA_INSUFFICIENT`（与推进剂不足严格区分）。

## 5. 输入校验与数据不足

**ValueError（搜索与状态推进前全部发现）**：rocket_ref 为空；stages/requirements 为空或类型错误；stage_index 或 burn_id 重复；requirements.order 不严格递增；非 calibration 燃烧 target 非有限正数；非 calibration requirement 已设置 preferred_stage_index；vehicle_mass_t/payload_t 非法；vehicle < payload + 全部 attached 级质量；attached/detached 拓扑不是"已分离前缀 + 仍连接后缀"；max_search_nodes 非非布尔正整数。

**DATA_INSUFFICIENT（不抛异常）**：任一仍可能参与任务的 attached 级 parallel=True 或 isp_vac_s=None；搜索达到 max_search_nodes。结果：success=False、requirements 全部真实燃烧保持未分配、`separations_before={}`、`planned_final_stages` 为输入独立副本、`planned_final_vehicle_mass_t == 初始质量`、warnings 指明原因。**搜索上限导致的数据不足与推进剂不足严格区分。**

## 6. 失败代码与现场快照

每次开始规划真实燃烧时保存**不可变现场信息**：burn_id、name、target_dv_m_s、working stages 副本、current_vehicle_mass_t、attached stage indices、每个候选级的失败代码、每个候选级在完成必要分离后的可用 Δv、候选级 ignition_count/multi_ignition、候选测试前被分离的级、候选测试时的整箭质量。

**失败诊断只使用该燃烧开始时的现场状态**，不得使用：整个任务的初始状态、其他搜索分支的状态、已回退的临时状态、后续候选产生的部分推进状态。

未找到方案时（按当前失败燃烧分类）：

| 情形 | failure_code |
|---|---|
| 没有任何 attached 候选级 | `NO_STAGE_ASSIGNED` |
| 所有候选均因 ignition_count>0 且 multi_ignition=False 被阻断 | `RESTART_UNSUPPORTED` |
| 至少一个合法候选进行了能力检查但 Δv 不足 | `PROPELLANT_INSUFFICIENT` |
| 出现 parallel、缺失比冲或候选评估上限 | `DATA_INSUFFICIENT` |

候选原因混合时优先级：`DATA_INSUFFICIENT` > `PROPELLANT_INSUFFICIENT` > `RESTART_UNSUPPORTED`；**未知或意外 failure_code 不默认为 RESTART_UNSUPPORTED**（按 DATA_INSUFFICIENT 处理）。

`failure_reason_zh` 基于当前失败现场列出：burn_id 和 name、目标 Δv、当前 attached 级、实际检查的候选级、每个候选级的主要失败原因。诊断 metadata 新增：`failure_burn_id`、`failure_burn_order`、`failure_attached_stage_indices`、`failure_snapshot_vehicle_mass_t`、`candidate_failure_codes`、`candidate_available_dv_m_s`、`aggregate_reachable_dv_m_s`、`whole_burn_limitation_triggered`（全部 JSON 可序列化）。

失败结果仍不泄漏半成品计划：requirements 中所有真实燃烧 `preferred_stage_index=None`、`separations_before={}`、`planned_final_stages` 为输入独立副本、`planned_final_vehicle_mass_t` 等于初始质量。

## 7. whole_burn_per_stage 判断（使用失败现场）

只有**同时满足**以下条件才声称失败与"单次燃烧不可跨级"有关：

1. 当前失败代码属于推进剂/Δv 不足。
2. 使用失败燃烧**开始前**的现场状态。
3. 至少两个仍可在该次机动中实际贡献正 Δv 的级。
4. 没有任何单级能独立完成目标燃烧。
5. 按从下到上串联顺序（含级间分离质量处理）计算，这些可贡献级的合计可用 Δv 在容差内达到目标。
6. `ignition_count>0` 且 `multi_ignition=False` 的级**不能贡献 Δv**，只能被分离。
7. parallel 或缺失比冲属于 DATA_INSUFFICIENT，不参与合计。
8. 不能使用任务开始前已被先前燃烧消耗掉的推进剂。
9. **单级火箭永远不触发"多级合计足够"提示。**

成立时：`failure_reason_zh` 明确说明；warnings 至少一条"多个级合计可能完成该次机动，但当前模型不支持一次 BurnRequirement 跨级"；`whole_burn_limitation_triggered=True`。不成立时：reason 不得出现 whole_burn_per_stage、不得产生跨级 warning、`whole_burn_limitation_triggered=False`。

合计 Δv 检查使用 `available_delta_v_m_s`，在现场状态副本上逐级更新：当前整箭质量、推进剂耗尽、干质量分离、级是否具备本次点火资格；不得重新实现不一致的火箭方程。

## 8. calibration 语义

`source='rocket_calibration'`：保留在输出 requirements 中；`preferred_stage_index=None`；不消耗推进剂；不增加 ignition_count；不生成分离事件；不参与候选级搜索；保持原 order 和 burn_id。

## 9. 计划自验证

规划器只生成计划，但成功计划必须能直接执行：

```python
final, mission = simulate_multistage_sequence(
    rocket_ref, original_parking_stages, plan.requirements,
    original_vehicle_mass_t, payload_t,
    separations_before=plan.separations_before, data_quality=...)
```

测试验证：`mission.success=True`；每个真实燃烧的执行级与计划一致；最终整箭质量与 `planned_final_vehicle_mass_t` 一致；每个最终级的 attached / ignition_count / propellant_remaining_t 与 `planned_final_stages` 一致。**规划器不得直接把 plan_verified 写成 True**；只有编排层真正执行后才能设置。

## 10. 测试

`tests/test_mission_stage_planner.py`（37 项）：

1. 当前活动级支持重启并完成全部后续燃烧。
2. 当前活动级不支持重启 → 分离后由上一级首次点火。
3. 某级完成一次真实燃烧后，第二次燃烧正确识别为重启。
4. 不支持第二次点火时向上换级（b1@0 → b2@1，计划可执行）。
5. 使用上一级前自动分离所有仍连接的下级（`{'b1': [0, 1]}` 顺序保留）。
6-7. 分离质量包含干质量与未使用推进剂；`discarded_propellant_t` 统计正确。
8. assignment 级位置不向下回退。
9. calibration 不参与分配、不改变状态。
10. 非连续 stage_index 正常工作且不排序。
11-13. 缺失比冲 / parallel → DATA_INSUFFICIENT 零状态推进；候选评估上限 → DATA_INSUFFICIENT（与推进剂不足区分）。
14. 无 attached 级 → NO_STAGE_ASSIGNED。
15. 重启能力是唯一阻断原因 → RESTART_UNSUPPORTED。
16-17. 推进剂不足 → PROPELLANT_INSUFFICIENT；多级合计足够但无单级足够 → 明确报告 whole_burn_per_stage 限制。
18. 输入非法 → 预校验 ValueError（含断裂拓扑）。
19-21. 输入完全不变；输出不共享引用；结果可直接 json.dumps。
22. 相同输入重复规划产生完全相同结果。
23. 失败时不泄漏搜索死胡同的部分分配或分离事件。
24-25. 成功计划交给 simulate_multistage_sequence 完整通过；最终质量与级状态与规划预测一致。
26. **候选级顺序尝试**：`test_falls_through_to_next_candidate_when_lower_stage_cannot_complete_burn`——同一燃烧先检查较低级（推进剂不足）后尝试更上级成功（候选评估，非跨燃烧回溯）。
27. **单级现场误报回归**：b1=6000、b2=3000、单级 100 t → b2 失败、PROPELLANT_INSUFFICIENT、无 whole_burn_per_stage、`aggregate_reachable_dv_m_s` 用 b2 前剩余推进剂计算、`whole_burn_limitation_triggered=False`。
28. **两级历史推进剂误用回归**：b1 消耗大量推进剂后，b2 现场两级别合计不足；任务开始前两级总能力却大于 b2 目标 → 不触发 whole_burn 提示。
29. **真正的跨级限制**：现场至少两个可点火级、单级均不足、串联合计足够 → PROPELLANT_INSUFFICIENT + reason 含 whole_burn_per_stage + warning 含跨级限制 + triggered=True。
30. **单级永不触发跨级提示**。
31. **重启不支持的级不计入 aggregate_reachable_dv_m_s**。
32. **混合失败原因优先级**：restart_unsupported + propellant_insufficient → PROPELLANT_INSUFFICIENT。
33. **意外 failure_code 不归类为 RESTART_UNSUPPORTED**（`_classify_failure_code` 直接验证）。
34. **失败诊断 burn_id 是实际首个无法规划的燃烧**。
35. **candidate_available_dv_m_s 使用候选分离后的整箭质量**。
36. **额外 untracked_mass_t 在全部正式分离与燃烧中保持不变**（计划与执行一致）。
37. **成功计划仍可执行且最终质量与级状态一致**。

## 11. Neutron I 2630 探针

A. **可行的小型后续任务**：2.5 t / 1581 m/s 校准停泊状态 + calibration + 两次较小真实燃烧 → 规划器选择第二级、两次判定为 RESTART、不再分离第二级、计划可被 simulate_multistage_sequence 成功执行。

B. **35786 km @ 180° combined**：`compute_orbit_mission` + `burn_sequence_to_requirements` 后，第一次后续点火约需 2450.7 m/s > 第二级剩余约 1581 m/s → 规划失败 `PROPELLANT_INSUFFICIENT`，不伪造执行级或部分成功计划，failure_reason_zh 指明失败燃烧与目标 Δv。该测试只证明"2.5 t 载荷不可行"，**不得**写成该火箭对该轨道的最大运力结论。

## 12. 当前仍未实现

- 载荷运力二分搜索（给定轨道求最大可交付载荷）——单载荷评估入口见
  `docs/MISSION_PAYLOAD_FEASIBILITY.md`（`evaluate_payload_for_mission` 已把
  停泊回放 + 本规划器 + 多级仿真组合为完整评估链，但仍只评估单一载荷）。
- API / 数据库 / YAML / 前端接入。
- 并联燃烧。
- 一次 BurnRequirement 跨多个级（whole_burn_per_stage=True 限制）。
- 单次机动跨级模型（未来替代 whole_burn_per_stage 的精细化模型）。
