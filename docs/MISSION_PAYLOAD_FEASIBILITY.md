# 单载荷轨道任务可行性评估（Single-Payload Mission Feasibility Evaluator）

阶段 3G 交付物：`mission_payload_evaluator.py`（项目根目录，纯 Python）。

## 1. 定位与边界

纯计算编排器，把既有管线组合为**单载荷**评估入口：

```
evaluate_payload_for_mission(
    rocket_ref, stages, requirements, dv_curve, payload_t,
    data_quality=DataQuality.EXACT, max_search_nodes=10000,
) -> PayloadFeasibilityResult
```

给定一枚已标准化的串联火箭（起飞前满推进剂 StageState）、`compute_rocket` 的 `dv_curve`、`burn_sequence_to_requirements` 的 BurnRequirement 列表和一个明确 `payload_t`：

1. 从 dv_curve 插值该载荷的校准剩余 Δv。
2. 用标准化级计算该载荷下的总真空 Δv。
3. 上升段需求 = 总真空 Δv − 校准剩余 Δv。
4. 回放火箭到停泊轨道。
5. 规划停泊轨道后多次点火与分离。
6. 用 `simulate_multistage_sequence` 验证计划。
7. 返回该载荷是否可行 + 每阶段审计结果。

**本模块只评估一个载荷**：不进行二分搜索、网格搜索或最大运力计算。评估模块本身不调用 `compute_rocket` / `compute_orbit_mission`（测试可以调用它们构造真实输入）。

## 2. 纯函数

### interpolate_calibrated_remaining_dv(payload_t, dv_curve) -> float

- 结构校验：≥2 个点、每点含 `m`/`dv`、数值有限非布尔、`m > 0` 且严格递增、`dv ≥ 0` 且随载荷非递增（极小容差）。格式错误、NaN、重复 m、倒序 m、负 dv → `ValueError`；不排序、不静默删点。
- 精确命中点返回该点 dv；点间线性插值；**不允许区间外外推或钳制**（越界抛 ValueError）。
- 返回值是 Python float（numpy.float64 自动转换）。

### compute_serial_stack_vacuum_dv(stages, payload_t) -> float

- 按输入顺序（从下到上）对每级调用 `available_delta_v_m_s`；级耗尽后扣除推进剂再扣除干质量，计算下一级（复用火箭方程，不重写）。
- detached 级不计入；parallel 或缺失比冲抛 ValueError（数据不足，不能当串联级计算）。

## 3. 为什么仍然需要停泊状态回放，而不能只比较总 Δv

`dv_curve` 是"进入停泊轨道后剩余能力"的校准来源；`总真空 Δv − 校准剩余` 推导出**有效上升段需求**（不是重新拟合大气损失，不读取 loss_model）。但总 Δv 只是理想堆栈上限——回放揭示：

- 哪些级在上升段耗尽并被分离（干质量是否还挂在整箭上）。
- 停泊轨道时的活动级、剩余推进剂与 `ignition_count`。
- 实际上升消耗是否与校准一致（`parking_remaining_dv_check` 自检）。

只比较总 Δv 会漏掉这些级状态，而后续任务规划完全依赖它们。

## 4. 评估流程与失败阶段

```
calibration_lookup →（插值/单调性/校准 vs 总Δv 一致性）
parking_replay    →（回放失败传播 replay failure_code）
parking_consistency→（停泊状态剩余 Δv 自检，容差 PARKING_DV_CHECK_TOLERANCE_M_S=1e-4）
stage_planning    →（规划失败传播 plan failure_code）
plan_verification →（仿真失败传播 simulation failure_code；规划/仿真不一致 → DATA_INSUFFICIENT）
None              →（成功）
```

- 校准范围外、曲线非单调、校准剩余 > 总真空 Δv（超容差）、上升段需求非正、计划验证不一致 → `DATA_INSUFFICIENT`（不猜测失败原因）。
- 非法调用参数 → `ValueError`。
- 失败结果保留已运行的子阶段（`parking_replay` / `stage_plan`）供审计，未运行阶段为 None。

## 5. 停泊轨道剩余 Δv 自检

回放成功后，用回放后的 attached 级与 `final_vehicle_mass_t` 按串联顺序（逐级耗尽、分离后更新质量）计算总剩余 Δv：

- `parking_remaining_dv_check_m_s`、`parking_remaining_dv_error_m_s`（= check − calibrated）。
- 超出 `PARKING_DV_CHECK_TOLERANCE_M_S` → `DATA_INSUFFICIENT`（阶段 `parking_consistency`），不继续规划。
- 正常情况误差为浮点噪声（~1e-13 m/s 量级）。

## 6. 计划验证（plan_verified）

规划成功后自动执行 `simulate_multistage_sequence`，并比较：

- `simulation.success`。
- 最终整箭质量与 `planned_final_vehicle_mass_t`（`MASS_TOLERANCE_T` 内）。
- 每级 `stage_index`/`attached`/`ignition_count`/`propellant_remaining_t` 与 `planned_final_stages` 一致。
- 每个真实 BurnResult.stage_index 与计划 `preferred_stage_index` 一致；calibration BurnResult.stage_index=None。

只有全部一致才返回 `feasible=True` 且 `metadata.plan_verified=True`。不一致 → `DATA_INSUFFICIENT`（阶段 `plan_verification`）、`plan_verified=False`、warnings 列出具体不一致字段；不静默采用任一方的值。**不修改** `stage_plan.metadata` 中原有的 `plan_verified=False`。

## 7. 数据质量

- 结果基于校准 dv_curve 的离散插值：**最高只能是 `ESTIMATED`**（即使输入 EXACT）。
- 输入 ESTIMATED 仍为 ESTIMATED；输入 INSUFFICIENT 直接返回 `DATA_INSUFFICIENT`（不执行任何子阶段）。
- 任一子阶段返回 INSUFFICIENT → 最终结果 INSUFFICIENT。
- warnings 至少说明：结果基于校准 dv_curve；未模拟有限推力、大气轨迹、上面级寿命和跨级单次燃烧；`whole_burn_per_stage=True`。

## 8. 当前限制

- 仅串联级（parallel → DATA_INSUFFICIENT）。
- 不支持单次燃烧跨级（whole_burn_per_stage=True）。
- 不支持有限推力、大气轨迹、级寿命/滑行约束。
- **不搜索最大载荷**——只回答"这一个载荷是否可行"。

## 9. 测试

`tests/test_mission_payload_evaluator.py`（32 项）：

1-3. dv_curve 精确命中 / 线性插值 / numpy.float64 → Python float。
4-5. 越界 → 评估 DATA_INSUFFICIENT（阶段 calibration_lookup，原因含载荷与范围）；纯函数拒绝且不钳制。
6-8. 结构错误（重复/倒序 m、NaN/Infinity、负 dv）→ ValueError；非单调曲线 → 评估 DATA_INSUFFICIENT（不静默修改）。
9-10. 串联总真空 Δv 逐级质量公式；detached 级不计入。
11. parallel / 缺失比冲 → DATA_INSUFFICIENT。
12. 校准剩余 > 总真空 Δv → DATA_INSUFFICIENT。
13-16. 回放/规划失败传播（DATA_INSUFFICIENT、RESTART_UNSUPPORTED、PROPELLANT_INSUFFICIENT），whole_burn 限制诊断保留。
17-21. 成功后自动仿真、执行级与计划一致、最终质量/级状态与预测一致、calibration BurnResult UNASSIGNED。
22-24. 停泊自检通过并记录；人为制造自检不一致（容差补丁）→ parking_consistency；人为制造规划/仿真不一致（模拟器补丁）→ 不返回 feasible=True。
25-26. 质量最高 ESTIMATED；输入 INSUFFICIENT 不执行子阶段。
27-30. 输入完全不变；输出不共享引用；全部结果可 json.dumps；重复评估确定性一致。
31-32. Neutron I 2630 集成：0.2 t 可行（第二级 ×2 RESTART、计划验证成功）；2.5 t 规划失败（PROPELLANT_INSUFFICIENT、阶段 stage_planning、校准剩余 ≈1581 m/s、无伪执行级）。

## 10. Neutron I 2630 集成结果

- **0.2 t**：feasible=True、quality=ESTIMATED、活动级=第二级、两次真实燃烧均为第二级 RESTART、plan_verified=True、停泊自检误差 ~1e-11 m/s、最终质量与计划预测一致（1.392645 t）。该结论只验证 0.2 t 这一离散载荷，不是最大运力。
- **2.5 t**：feasible=False、PROPELLANT_INSUFFICIENT、阶段 `stage_planning`、校准剩余 ≈1581 m/s 而首次后续点火需 ≈2450.7 m/s、simulation=None、无伪执行级。

## 11. 下一阶段仍未实现

- 最大载荷运力二分/网格搜索——单载荷评估器**不会自动搜索**；最大载荷搜索由
  `mission_capacity_search.find_max_payload_for_mission` 在 dv_curve 校准范围内
  重复调用本评估器完成，见 `docs/MISSION_CAPACITY_SEARCH.md`。
- API、SQLite、前端接入。
- 并联燃烧、单次燃烧跨级模型。
