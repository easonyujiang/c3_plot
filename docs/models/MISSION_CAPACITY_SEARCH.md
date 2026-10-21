# 轨道任务最大载荷搜索（Mission Capacity Search）

阶段 3H 交付物：`c3_mass/mission_capacity_search.py`（c3_mass 包目录，纯 Python）。

## 1. 定位与边界

在 dv_curve 的合法载荷范围内重复调用 `evaluate_payload_for_mission`，为"指定火箭 × 指定任务"求出**最大可验证载荷区间**。

```
find_max_payload_for_mission(
    rocket_ref, stages, requirements, dv_curve,
    data_quality=DataQuality.EXACT,
    payload_tolerance_t=0.001,
    monotonicity_samples=17,
    max_evaluations=128,
    max_search_nodes=10000,
) -> MissionCapacityResult
```

搜索器**不重新实现**：停泊轨道回放、级分配、多次点火判断、火箭方程、任务仿真。单载荷可行性的唯一权威来源是 `evaluate_payload_for_mission`。

依赖：标准库 + `c3_mass/mission_simulation.py` + `c3_mass/mission_payload_evaluator.py`。不依赖 Flask / SQLite / `server.models` / 前端 / YAML / .craft-VAB / NumPy（测试可以调用 compute_rocket、compute_orbit_mission 和真实 YAML 构造输入）。

## 2. 搜索只在 dv_curve 校准范围内进行

- 范围严格为 `[calibration_payload_min_t, calibration_payload_max_t]`（`calibration_payload_bounds(dv_curve)`，复用单载荷评估器的同一套校验，不复制第二份不一致逻辑）。
- 禁止：搜索 0 到无穷、把最小点替换为 0、校准范围外插值、按火箭湿质量猜测上界。
- 格式错误 / 非单调曲线 → `ValueError`，不静默修复。

## 3. 为什么先做单调性采样，再二分

`grid_guard_then_bisection`：

1. **单调性保护采样**：在校准范围内生成 `monotonicity_samples` 个等距点（含两个端点），逐点评估。合法单调形式只能是 `可行…可行 → 不可行…不可行`。
2. 若出现"不可行之后又可行" → `capacity_status='non_monotonic'`、`success=False`、`DATA_INSUFFICIENT`、**不进行二分**、warnings 列出发生反转的载荷点、不得把最高可行样本冒充最大载荷。
3. 取相邻的最高可行样本与最低不可行样本作为初始边界，进入二分细化，直到 `high - low <= payload_tolerance_t`。

**有限采样只是防护，不是对连续单调性的数学证明**：它不能证明不存在狭窄可行岛。文档明确这一限制，warnings 也提示"单调性仅经过有限采样保护"。

## 4. max_payload_t 为什么取已验证可行下界

二分收敛后的区间 `[low, high]` 中，只有 `low` 被实际评估过且可行。因此：

- `max_payload_t = low`（已验证可行）。
- 不得使用区间中点（未必已验证可行）。
- 不得返回 `high`（不可行）。
- 不得提前四舍五入；不得用快速估算结果替代详细评估。

## 5. 端点与失败状态

| capacity_status | 含义 | max_payload_t |
|---|---|---|
| `bounded` | 校准范围内找到"可行下界 + 不可行上界"，宽度 ≤ 容差 | 可行下界 |
| `below_calibration_range` | 最小校准载荷仍不可行；真实最大可能更低 | None（不得返回 0 冒充） |
| `above_calibration_range` | 最大校准载荷仍可行；真实最大可能更高 | None（不得把校准上限冒充） |
| `data_insufficient` | 某个关键单载荷评估 DATA_INSUFFICIENT（采样或二分中）或边界审计失败 | None |
| `non_monotonic` | 采样发现不可行后再次可行 | None |
| `evaluation_limit` | 达到 `max_evaluations` 仍未满足误差 | None（不得把未达精度的 low 冒充最终值） |

**DATA_INSUFFICIENT 不能当作普通不可行**：不可行推进边界，DATA_INSUFFICIENT 是"不知道"，二者语义不同，搜索必须停止而不是继续二分。

## 6. 评估缓存与摘要

- 所有载荷评估经内部缓存（键 = 评估时的 Python float 载荷值）：相同载荷绝不重复调用。
- `evaluations` 按载荷升序保存**摘要**（payload_t / feasible / failure_code / failure_phase / data_quality / calibrated_remaining_dv_m_s / assigned_stage_indices），不嵌套完整仿真对象。
- `best_feasible_evaluation` 与 `first_infeasible_evaluation` 保留完整 `PayloadFeasibilityResult`。

## 7. 边界审计（bounded 必须满足）

- `best_feasible_evaluation.feasible=True` 且 `plan_verified=True`。
- `first_infeasible_evaluation.feasible=False` 且 `failure_code != DATA_INSUFFICIENT`。
- 两者载荷差 ≤ `payload_tolerance_t`。
- `evaluations` 包含这两个边界点。

任一不成立 → `capacity_status='data_insufficient'`、`max_payload_t=None`，不发布最大值。

## 8. 数据质量

- `bounded` 结果的 `data_quality` **不得优于** `best_feasible_evaluation.data_quality`（校准插值模型下通常为 ESTIMATED）。
- 其他未确定精确最大值的状态 → `INSUFFICIENT`。
- warnings 至少包含：基于 dv_curve 校准插值；仅在校准载荷范围内搜索；单调性仅经有限采样保护；`whole_burn_per_stage=True`；未模拟有限推力、级寿命和并联燃烧。

## 9. 测试

`tests/test_mission_capacity_search.py`（26 项）：

1-3. 单调阈值 → bounded；max 用可行下界（不用中点/上界）；边界宽度 ≤ 容差。
4-5. 全部不可行 → below_calibration_range；全部可行 → above_calibration_range。
6. 采样/二分中 DATA_INSUFFICIENT → data_insufficient。
7-8. 不可行后再次可行 → non_monotonic 且不执行二分。
9. 达到 max_evaluations → evaluation_limit（保留 low/high 供审计）。
10-12. 相同载荷只评估一次；evaluations 升序；只含摘要。
13. best/first 边界结果正确。
14-16. 非法容差 / 采样数 / 评估上限 → ValueError（含 max < samples）。
17. 畸形/非单调 dv_curve 不静默修复 → ValueError。
18-19. 输入完全不变；输出不共享引用。
20-21. 全部状态可 json.dumps；重复搜索确定性一致。
22-23. bounded 为 ESTIMATED；未确定状态为 INSUFFICIENT。
24. 单载荷失败阶段保留在摘要中。
25. max_search_nodes 原样传递。
26. 真实端到端（无 mock）：合成串联火箭 + 一致单调曲线 + 真实评估器 → bounded、上下界审计通过、两级任务由上面级执行。

## 10. Neutron I 2630 集成结果（实测）

```
35786 km @ 180° combined，payload_tolerance_t=0.001，samples=17，max=128
status: bounded | success: True | quality: estimated
max_payload_t: 0.502225 t（可行下界，plan_verified=True，执行级 [1, 1]）
feasible_lower: 0.502225 | infeasible_upper: 0.502829 | 宽度: 0.000604 ≤ 0.001
evaluations: 26（17 采样 + 9 二分）| cache_hits: 0
不可行上界：propellant_insufficient @ stage_planning
```

这是**模型结果**（calibrated_serial_multiburn + whole_burn_per_stage），不是现实飞行认证值，也不是不带模型限制的火箭绝对运力；会随校准点、发动机数据和模型更新改变。

## 11. 当前限制

- 仅串联级；不支持单次燃烧跨级；不支持有限推力与级寿命。
- 单调性仅经有限采样保护。
- 尚未接入 find_rockets_for_orbit / API / 数据库 / 前端。

> 后端服务层（按火箭 × 任务组合容量搜索与单载荷评估并生成 API 摘要）见
> `docs/models/MISSION_ANALYSIS_SERVICE.md`。本搜索算法本身未被服务层修改。
