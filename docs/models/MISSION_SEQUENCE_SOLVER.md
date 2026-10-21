# 顺序任务求解器（Sequential Stage Mission Solver）

阶段 3B 交付物：`c3_mass/mission_solver.py` 中的 `simulate_stage_sequence()`（纯 Python）。

## 1. 定位与边界

这是**单个明确执行级**的顺序求解器：在调用方显式传入的一个 `StageState` 上，按输入顺序逐条执行 `BurnRequirement`，并汇总为 `MissionSimulationResult`。

> **多级版本见 `docs/models/MISSION_MULTISTAGE_SOLVER.md`**：`simulate_multistage_sequence()` 支持多个显式 StageState 与显式分离事件（`separations_before`）。本函数不描述多级能力——它只在一个明确指定的级上执行。

```
simulate_stage_sequence(
    rocket_ref: str,
    stage: StageState,
    requirements,          # list/tuple of BurnRequirement，非空
    vehicle_mass_t: float, # 序列开始前整箭质量（t）
    payload_t: float,      # 任务载荷（t）
    data_quality: DataQuality = DataQuality.EXACT,
) -> (final_stage_state, mission_result)
```

**本阶段仍不支持**：

- 多级自动分配（选择执行级）。
- 分级（staging）事件。
- 从火箭 YAML / 配置构造停车轨道初始 `StageState`。
- 并联助推器燃烧。
- 自动判断哪个级完成入轨。
- `compute_orbit_mission` 到 BurnRequirement 的转换。
- API、数据库和前端。

输入顺序即执行顺序，**从不暗中排序**。任何输入对象（stage、requirements 及其嵌套 metadata）都不被修改。

## 2. 输入校验（非法调用参数抛 ValueError）

- `stage` 必须是 `StageState`。
- `rocket_ref` 非空字符串。
- `requirements` 必须是非空 list / tuple；每一项必须是 `BurnRequirement`。
- `burn_id` 必须唯一。
- `order` 必须严格递增。
- `vehicle_mass_t` 有限正数；`payload_t` 有限非负，且 `payload_t ≤ vehicle_mass_t`（容差内）。
- 非校准燃烧的 `target_dv_m_s` 必须有限正数。

### 2.1 序列初始质量守恒（`_validate_sequence_initial_mass`）

`vehicle_mass_t` 是整箭总质量，至少必须包含：

- **attached 执行级的当前质量**：`stage.dry_mass_t + stage.propellant_remaining_t`
- **payload_t**（级质量之外的任务载荷）
- 可选的其他连接质量（其他仍连接的结构或级）

最小整箭质量公式（仅 `stage.attached=True` 时）：

```
required_minimum = stage.dry_mass_t + stage.propellant_remaining_t + payload_t
```

- 允许额外质量；`vehicle_mass_t + MASS_TOLERANCE_T < required_minimum` → `ValueError`。
- 错误信息显示：`vehicle_mass_t`、当前级质量、`payload_t`、所需最小总质量。
- **不静默修正 vehicle_mass_t**。
- **detached 级例外**：`stage.attached=False` 时该级质量**不计入**整箭质量下限——普通真实燃烧仍由 `execute_burn` 返回 `NO_STAGE_ASSIGNED`，而 calibration-only 序列可以继续作为纯记录通过。

## 3. 显式执行级约束

对于 `source != 'rocket_calibration'` 的燃烧：

- `preferred_stage_index is None` 或等于 `stage.stage_index` → 使用传入的 stage。
- 不一致 → 不抛异常、**不自动寻找另一级**；生成失败 BurnResult（`NO_STAGE_ASSIGNED`、`ignition_kind=UNASSIGNED`、`stage_index=None`、actual=0、消耗 0、质量不变），任务在此停止。

## 4. 校准覆盖燃烧（source='rocket_calibration'）

不调用 `execute_burn`，不扣除推进剂，不增加 `ignition_count`。生成合法 BurnResult：

- `success=True`，`failure_code=NONE`，`failure_reason_zh=''`
- `ignition_kind=UNASSIGNED`，`stage_index=None`
- `actual_dv_m_s=None`，`mass_before_t=mass_after_t=当前整箭质量`
- `propellant_consumed_t=0`，`propellant_remaining_t=None`
- `data_quality` 使用传入值

**校准覆盖燃烧不会推断当前执行级是否参与了入轨**。序列开始时传入的 `stage.ignition_count` 是后续 FIRST / RESTART 判定的**权威状态**：

- `ignition_count=0`：校准项之后的首次真实燃烧仍是 `FIRST`。
- `ignition_count=1`：校准项之后的真实燃烧是 `RESTART`。
- 列表中出现的校准燃烧**不会**自动增加 `ignition_count`。

## 5. 顺序执行与失败停止

普通燃烧的循环：

1. 用当前 `StageState` 与当前 `vehicle_mass_t` 调用 `execute_burn`。
2. 用返回的新 StageState 替换当前状态；用 `BurnResult.mass_after_t` 更新当前整箭质量。
3. 把 BurnResult 加入结果列表；成功则继续下一项。

**第一次失败后立即停止**，不再尝试后续燃烧。**未尝试的 requirement 不生成伪造 BurnResult**，也不使用错误的失败码——`burns` 只包含已经执行或处理的项目。

## 6. 任务汇总

- 成功：所有 requirements 都已处理 且 所有 `BurnResult.success` 均为 True → `success=True`、`failure_code=NONE`、`failure_reason_zh=''`。
- 失败：`failure_code` / `failure_reason_zh` 取**第一个失败 BurnResult** 的值。

`MissionSimulationResult.metadata`（全部可 JSON 序列化）：

| 字段 | 含义 |
|---|---|
| `initial_vehicle_mass_t` | 序列开始前整箭质量 |
| `final_vehicle_mass_t` | 序列结束时整箭质量（含推进剂不足失败的已烧消耗） |
| `total_requirement_count` | requirement 总数 |
| `completed_burn_count` | 已执行/已处理的项目数（= len(burns)） |
| `stopped_at_burn_id` | 第一个失败项；成功为 `None` |
| `executing_stage_index` | 求解器的执行级 |

## 7. 数据质量聚合

质量从好到差：`EXACT < ESTIMATED < INSUFFICIENT`（用**显式等级映射**，不依赖字符串字典序）。任务质量取所有已产生 BurnResult 中最差的等级。requirements 非空 ⇒ 必有结果，不会出现"无结果可聚合"。

## 8. 燃烧上下文

每个由求解器生成或收集的 BurnResult，其 `metadata` 至少包含：

- `source`：requirement.source
- `name`：requirement.name
- `requirement_metadata`：requirement.metadata 的**深拷贝**（与 requirement 不共享可变引用）

`execute_burn` 的公开签名未修改。

## 9. 返回值

`(final_stage_state, mission_result)`：

- `final_stage_state` **总是新对象**（即使第一项就失败，也不是传入的 stage 实例）。
- 成功时反映全部推进剂消耗与 `ignition_count`。
- 失败时反映失败发生前已成功完成的燃烧。
- **推进剂不足失败已经真实点火**，因此保留 `execute_burn` 返回的耗尽状态（推进剂 0、ignition_count 已增加）。
- 重启不支持 / 数据不足 / 执行级不匹配等**未实际点火**的失败不消耗推进剂。

## 10. 测试

`tests/test_mission_sequence.py`（28 项）+ `tests/test_mission_solver.py` 中新增 7 项修复测试：

1. stage 类型错误稳定抛 ValueError（不是 AttributeError）。
2. 容差成功分支严格质量守恒。
3. 两次顺序燃烧使用逐步递减的整箭质量与剩余推进剂，ignition_count 正确增长。
4. 校准覆盖项不消耗推进剂、不增加 ignition_count。
5. `ignition_count=0` 时校准项后的真实燃烧为 FIRST。
6. `ignition_count=1` 时校准项后的真实燃烧为 RESTART。
7. 不支持重启：任务失败并停止。
8. 推进剂不足：保留耗尽状态，后续 requirement 不执行。
9. `preferred_stage_index` 匹配成功。
10. 不匹配返回 NO_STAGE_ASSIGNED 并停止。
11-15. 空 requirements / 重复 burn_id / order 不严格递增 / 非 BurnRequirement 元素 / payload 超整箭质量 → ValueError。
16. data_quality 取最差等级（含 isp 缺失强制 INSUFFICIENT 的聚合）。
17. metadata 含 source / name / requirement_metadata。
18. 修改结果 metadata 不影响 requirement.metadata。
19. 结果可直接 json.dumps。
20. 输入 stage / requirements / metadata 完全不被修改。
21. 第一项失败时返回的 final_stage 仍是新对象（六种失败路径）。
22. 成功任务 initial/final vehicle mass 与每次燃烧质量连续一致。
23. **序列初始质量约束**：stage 质量 12 t + payload 1 t + vehicle 5 t（仅 calibration 项）→ 拒绝；12 + 5 + vehicle 13 → 拒绝（最低 17 t）；vehicle 17 → 接受；vehicle 20（3 t 额外结构）→ 接受；attached=False + calibration-only → 接受（级质量不计入下限）；被拒调用不修改输入。

数学期望值均使用独立公式或手工常数计算。
