# 显式多级求解器（Explicit Multistage Mission Solver）

阶段 3C 交付物：`mission_solver.py` 中的 `simulate_multistage_sequence()`（纯 Python）。

## 1. 定位与边界

在**多个显式 StageState** 上执行顺序任务仿真。调用方必须明确指出：

- 每次真实燃烧由哪个级执行（`preferred_stage_index`，**强制显式输入**）。
- 哪些级在哪些燃烧之前分离（`separations_before`）。

求解器**从不**自动选择级、自动分级、自动推测火箭构型，也不判断哪一级完成入轨。

```
simulate_multistage_sequence(
    rocket_ref: str,
    stages,                    # 非空 list/tuple of StageState，stage_index 唯一（允许不连续）
    requirements,              # 非空 list/tuple of BurnRequirement
    vehicle_mass_t: float,     # 序列开始前整箭质量（t）
    payload_t: float,          # 任务载荷（t）
    separations_before=None,   # dict[burn_id, list[stage_index]]
    data_quality: DataQuality = DataQuality.EXACT,
) -> (final_stage_states: list[StageState], mission_result)
```

- 返回的级列表**保持输入顺序**，每个元素都是新对象。
- `separations_before=None` 等价于没有分离事件。
- 事件顺序只跟随 `requirements` 顺序，**不依赖字典遍历顺序**。
- 复用 `execute_burn`（多级求解不重新实现火箭方程）；现有 `execute_burn` / `simulate_stage_sequence` 公开行为不变。

## 2. 显式执行级规则

对于 `source != 'rocket_calibration'` 的 requirement：

- `preferred_stage_index` 必须提供；缺失 → `ValueError`（调用参数错误）。
- 指定的 stage_index 不存在 → 预校验 `ValueError`。
- 指定级存在但在执行时**已经 detached**（被更早事件分离）→ 生成失败 BurnResult：`NO_STAGE_ASSIGNED`、`ignition_kind=UNASSIGNED`、`stage_index=None`、不消耗推进剂、不增加 ignition_count、任务立即停止。
- 不得自动寻找其他可用级；不得按 stage_index 大小判断上下级；不得按"剩余推进剂最多"或"最后一级"自动分配。

`source='rocket_calibration'` 保持单级求解器语义：

- 不要求 `preferred_stage_index`。
- 不消耗推进剂、不增加任何级的 ignition_count。
- `ignition_kind=UNASSIGNED`。
- 不得据此推断哪个级参与了入轨。

## 3. separations_before 事件时序

每次 requirement 的处理顺序：

1. 查找该 burn_id 的显式分离事件。
2. 执行分离。
3. 再执行校准记录或真实燃烧。

分离一个仍连接的级：

- 丢弃质量 = 该级当时的 `dry_mass_t + propellant_remaining_t`。
- 从当前整箭质量中扣除全部丢弃质量。
- 返回的新 StageState 置 `attached=False`，保留 dry/wet/propellant/ignition_count 供审计。
- 分离本身不改变 ignition_count。
- **推进剂耗尽后干质量仍留在整箭上**，直到显式分离；禁止自动丢弃干质量。

### 3.1 分离计划预校验（状态推进前全部完成）

- burn_id 必须存在于 requirements。
- calibration requirement 前不允许安排分离。
- stage_index 必须存在。
- 同一级不得被安排分离两次（跨事件或事件内重复索引均拒绝）。
- 初始已 detached 的级不得安排分离。
- 同一次燃烧前不得分离该燃烧明确指定的执行级。
- 空分离列表、布尔值冒充整数、非 int 条目 → `ValueError`。
- 非法计划必须在任何状态推进前被拒绝，不留下部分执行结果。

**允许**后续 requirement 指定一个在更早事件中已分离的级：这不是调用格式错误，执行到该 requirement 时返回 `NO_STAGE_ASSIGNED`。

## 4. 整箭质量守恒

初始整箭质量至少包含：

```
payload_t + Σ(所有 attached=True 级的 (dry_mass_t + propellant_remaining_t))
```

未建模质量：

```
untracked_mass_t = vehicle_mass_t - payload_t - Σ(初始连接级当前质量)
```

- `untracked_mass_t` 在 `MASS_TOLERANCE_T` 内不得为负，否则 `ValueError`。
- 未建模质量本阶段**始终保留**，不得自动丢弃。
- 燃烧时向 `execute_burn` 传入当前整箭质量；成功或耗尽燃烧后整箭质量减少实际消耗的推进剂。
- 分离后整箭质量减少该级当时的干质量 + 剩余推进剂。
- 每一步都在 `MASS_TOLERANCE_T` 内满足：

```
current_vehicle_mass = payload_t + untracked_mass_t + Σ(attached 级当前质量)
```

- 不得通过静默修改 vehicle_mass_t、推进剂或级质量掩盖不守恒（求解器末尾有防御性守恒校验，超出容差即抛 `ValueError`）。

## 5. 失败与状态提交语义

- 第一项失败后立即停止；未处理的 requirement 不生成伪 BurnResult。
- 失败前已完成的燃烧和分离保留在最终状态中。
- `PROPELLANT_INSUFFICIENT`：推进剂已实际耗尽，状态需要提交（推进剂 0、ignition_count +1）。
- `RESTART_UNSUPPORTED` / `DATA_INSUFFICIENT` / `NO_STAGE_ASSIGNED` 等未实际点火路径不消耗推进剂。
- `parallel=True` 的级被指定执行真实燃烧时沿用现有 `DATA_INSUFFICIENT` 行为；不得自动组合并联助推器与芯级。

## 6. 审计 metadata

`MissionSimulationResult.metadata`：

| 字段 | 含义 |
|---|---|
| `initial_vehicle_mass_t` | 序列开始前整箭质量 |
| `final_vehicle_mass_t` | 序列结束时整箭质量 |
| `initial_attached_stage_mass_t` | 初始所有 attached 级质量之和 |
| `final_attached_stage_mass_t` | 结束时 attached 级质量之和 |
| `untracked_mass_t` | 未建模质量（全程不变） |
| `stage_indices` | 输入级顺序的 stage_index 列表 |
| `total_requirement_count` | requirement 总数 |
| `completed_burn_count` | 已处理项目数 |
| `stopped_at_burn_id` | 首个失败项；成功为 None |
| `separation_events` | 分离事件列表 |

`separation_events` 每项：

| 字段 | 含义 |
|---|---|
| `before_burn_id` | 分离发生在哪次燃烧之前 |
| `stage_indices` | 本次事件分离的级索引列表 |
| `discarded_mass_t` | 本次事件丢弃总质量 |
| `mass_before_t` / `mass_after_t` | 事件前后整箭质量 |

全部 metadata 可 JSON 序列化，且不与输入对象共享嵌套引用。

## 7. 测试

`tests/test_mission_multistage.py`（26 项）：

1. 两级火箭：一级燃烧 → 一级分离 → 二级首次点火（且**不被误判为 restart**）。
2. 分离质量 = 干质量 + 未使用推进剂。
3. 推进剂耗尽但未分离时，干质量仍属于整箭（后续燃烧质量包含它）。
4. 二级首次点火 = FIRST。
5. 同一级第二次点火 = RESTART。
6. 不支持重启 → 正确失败并停止。
7. 分离后再次指定该级 → NO_STAGE_ASSIGNED（质量不变、不点火）。
8. 不存在的 preferred_stage_index → 预校验 ValueError。
9. 非校准燃烧缺少 preferred_stage_index → ValueError。
10. 重复 stage_index → ValueError。
11. 非连续 stage_index（如 [1, 3]）正常执行。
12. 重复分离 / 分离不存在级 / 分离已脱离级 / 空列表 / 布尔索引 / 事件内重复 → ValueError。
13. 同一次燃烧前分离其执行级 → ValueError。
14. calibration 不改变任何级状态，其后真实燃烧仍为 FIRST。
15. calibration 前安排分离 → ValueError。
16. parallel 执行级 → DATA_INSUFFICIENT，不自动组合其他级。
17. untracked_mass_t 在全部燃烧与分离过程中保持不变。
18. 中途推进剂不足：保留此前状态与耗尽状态，后续 requirement 不执行。
19. 输入对象及嵌套 metadata 完全不变。
20. 输出级状态与 metadata 不与输入共享可变引用。
21. 结果可直接 json.dumps。
22. 每次燃烧、分离与最终质量均通过独立公式验证守恒。

期望值使用独立公式（`-math.expm1` / `-math.log1p`）或人工常数计算，不调用被测函数。

## 8. 当前仍不支持的能力

- 从火箭配置 / YAML 自动生成 StageState（normalize_stages）。
- 自动选择执行级。
- 自动插入分离事件。
- 自动推断停泊轨道由哪个级完成。
- 并联级联合燃烧。
- 多条飞行方案搜索或最优级分配。
- 分级事件（staging）之外的自动质量处理。
- Flask API、数据库、YAML、前端接入。

> 输入适配层（配置级 → StageState、burn_sequence → BurnRequirement）见
> `docs/MISSION_INPUT_ADAPTERS.md`。本求解器本身仍只接受调用方显式给出的
> StageState、`preferred_stage_index` 与 `separations_before`，不改变求解语义。
