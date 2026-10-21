# 单级燃烧执行原语（Single-Stage Burn Primitive）

阶段 3A 交付物：`c3_mass/mission_solver.py`（c3_mass 包目录，纯 Python）。

## 1. 目的与边界

给定**明确的执行级**、一次**正 Δv 燃烧要求**和**整箭当前质量**，计算推进剂消耗，返回新的 `StageState` 与 `BurnResult`。

**本阶段不支持（由后续阶段实现）**：

- 自动级分配（哪个 stage 执行哪次点火）。
- 分级（staging）事件。
- 并联（booster）燃烧——并联级需要组合求解。
- 校准曲线覆盖的燃烧（`source='rocket_calibration'`）——应直接生成 UNASSIGNED 成功结果，不扣推进剂。
- 完整任务汇总（MissionSimulationResult 聚合）。
- API 与前端接入。

模块约束：只依赖标准库与 `c3_mass/mission_simulation.py`；不 import Flask / SQLite / `server.models` / `compute_c3_mass` / 前端；不读写文件；不修改输入对象；无第三方依赖。

## 2. vehicle_mass_t 的含义

`vehicle_mass_t` 是**燃烧前整枚当前载具的总质量**，包括：

- 当前仍连接的所有级（含尚未分离的上面级/载荷级）。
- 当前执行级本身。
- 载荷。

**当前级质量 vs 整箭质量的区别**：当前级质量下限是 `dry_mass_t + propellant_remaining_t`（级干质量 + 级内剩余推进剂）；整箭质量必然 ≥ 该下限（还有载荷与上级质量）。若 `vehicle_mass_t < dry + propellant`，说明调用方传入了不一致的质量，抛 `ValueError`。火箭方程的质量比与质量单位无关，因此所有质量直接用**吨**，不需要换算成千克。

## 3. 两个火箭方程公式

常量：`STANDARD_GRAVITY_M_S2 = 9.80665`（m/s²）；`MASS_TOLERANCE_T = 1e-9`（t，仅用于比较，不用于取整）。

### 3.1 available_delta_v_m_s(stage, vehicle_mass_t) → float

最大可用 Δv（耗尽当前级全部剩余推进剂）：

```
available_dv = isp_vac_s × g0 × ln( vehicle_mass_t / (vehicle_mass_t − propellant_remaining_t) )
```

### 3.2 required_propellant_t(stage, vehicle_mass_t, target_dv_m_s) → float

达到目标 Δv 所需的理论推进剂：

```
required = vehicle_mass_t × ( 1 − exp( −target_dv_m_s / (isp_vac_s × g0) ) )
```

两个公式互为反算（质量容差内）。`target_dv_m_s` 必须是有限正数；本阶段不允许 0。返回值**不截断**到级剩余推进剂——是否足够由 `execute_burn` 判断。

### 数值稳定性（expm1 / log1p）

数学表达式保持上式不变，但代码使用数值稳定形式避免浮点消减：

- `required_propellant_t` 实现为 `vehicle_mass_t * (-math.expm1(-x))`。直接用 `1.0 - math.exp(-x)` 时，x 极小（如 target=1e-14）会令 `exp(-x)` 舍入为 1.0，错误得到 0 推进剂。
- `available_delta_v_m_s` 实现为先算推进剂占比 `fraction = propellant_remaining_t / vehicle_mass_t`，再用 `isp_vac_s * g0 * (-math.log1p(-fraction))`。**不能先形成接近 1 的质量比再取 log**：推进剂相对整箭极小时，`vehicle / (vehicle − propellant)` 会先被舍入成 1.0，再得到 0 Δv。
- `fraction` 必须满足 `0 ≤ fraction < 1`；内部状态违反该范围抛 `ValueError`（不产生复数、NaN 或除零）。
- 不做四舍五入，不引入人为最小推进剂值；正的 target 必得正的 required，极小正推进剂必得正 Δv。普通 Δv 下的结果与旧表达式一致（仅浮点末位差异）。

### 输入验证（两个纯函数共用）

`vehicle_mass_t` 有限正数；`stage.attached=True`；`stage.parallel=False`；`stage.isp_vac_s` 存在且 > 0；`vehicle_mass_t ≥ dry + propellant`（容差内）。违反即抛 `ValueError`，不修改 stage。

## 4. execute_burn(stage, requirement, vehicle_mass_t, data_quality=EXACT)

返回 `(新 StageState, BurnResult)`，输入一律不被修改。

### 4.1 基础输入（ValueError）

- **`stage` 必须先是 `StageState`**（在任何 `stage.attached` / `stage.parallel` 等属性访问之前检查；传 dict / None / 任意对象一律抛 `ValueError`，不会泄漏 `AttributeError`）。
- `requirement` 必须是 `BurnRequirement`。
- `target_dv_m_s` 必须严格大于 0（本函数不处理 0 Δv / 校准覆盖燃烧）。
- `source='rocket_calibration'` 抛 ValueError，明确说明应由任务求解器生成 UNASSIGNED 成功结果，不能在此重复扣除推进剂。

### 4.2 点火类型判定（燃烧前 stage.ignition_count）

- `ignition_count == 0` → `IgnitionKind.FIRST`（首次点火**不要求** `multi_ignition=True`）。
- `ignition_count > 0` → `IgnitionKind.RESTART`。

### 4.3 任务不可行分支（返回失败 BurnResult，不抛异常；StageState 等值不变）

| 条件 | failure_code | ignition_kind / stage_index | 其他 |
|---|---|---|---|
| `attached=False` | `NO_STAGE_ASSIGNED` | UNASSIGNED / None | actual=0，消耗 0，质量不变，中文原因非空 |
| `parallel=True` | `DATA_INSUFFICIENT` | UNASSIGNED / None | 原因明确说明"并联燃烧需要组合求解"；data_quality 强制 INSUFFICIENT |
| `isp_vac_s=None` | `DATA_INSUFFICIENT` | 按 ignition_count（FIRST/RESTART）/ 当前级 | actual=0，消耗 0；data_quality 强制 INSUFFICIENT；显式处理，不靠捕获底层 ValueError |
| `ignition_count>0` 且 `multi_ignition=False` | `RESTART_UNSUPPORTED` | RESTART / 当前级 | actual=0，消耗 0，ignition_count 不增加，data_quality 使用传入值 |

### 4.4 推进剂充足（required ≤ remaining + MASS_TOLERANCE_T）

- `success=True`，`failure_code=NONE`，`failure_reason_zh=''`。
- `actual_dv_m_s` = 目标值（避免浮点反算偏差）。
- **消耗 = `min(required, remaining)`**：当 required 仅在容差范围内略大于 remaining 时，这是数值容差成功，**不是允许创造推进剂**——绝不能从级中消耗超过 remaining 的推进剂。
- `mass_after = vehicle − consumed`，整箭质量减少量严格等于 consumed。
- 新级：`propellant_remaining_t = remaining − consumed`（≥ 0，容差取负则归零，**不做四舍五入**），`ignition_count` 增加 1，其余字段不变。
- `BurnResult.propellant_consumed_t` 等于 consumed。

### 4.5 推进剂不足

模拟发动机工作至推进剂耗尽：

- 消耗**全部**剩余推进剂（已经发生实际点火）。
- `actual_dv_m_s` 通过火箭方程（`available_delta_v_m_s`）计算。
- `success=False`，`failure_code=PROPELLANT_INSUFFICIENT`，中文原因包含所需/剩余推进剂与缺少的 Δv。
- 新级：`propellant_remaining_t=0`，`ignition_count` 增加 1（即使任务失败，点火已经发生）。
- 成功结果中的 actual 用目标值；不足结果中的 actual 必须来自火箭方程。

## 5. 为什么推进剂不足时要消耗全部推进剂

发动机一旦点火就会持续工作；单级原语没有"中途关机"的能力假设。若理论所需推进剂大于剩余，物理上发动机将一直烧到推进剂耗尽，提供 `available_delta_v_m_s` 给出的实际 Δv，任务在缺少 `target − actual` m/s 时失败。失败仍要记录这次真实点火（`ignition_count + 1`），否则后续燃烧会错误地复用本已耗尽的推进剂。

## 6. 测试

`tests/test_mission_solver.py`（38 项）：

1. `available_delta_v_m_s` 已知数值（手算常数 656.487 m/s 与独立公式）。
2. `required_propellant_t` 已知数值（≈15.629 t 与独立公式）。
3. 两个公式互为反算。
4. 首次点火成功（FIRST、count 0→1、质量正确减少；不要求 multi_ignition）。
5. 同一级第二次燃烧成功（RESTART、count 1→2、不重复使用已消耗推进剂）。
6. 不支持重启（RESTART_UNSUPPORTED、状态完全不变）。
7. 推进剂不足（耗尽、返回实际 Δv、count 增加）。
8. isp 缺失（DATA_INSUFFICIENT、状态不变、quality 强制 INSUFFICIENT）。
9. 已分离级（NO_STAGE_ASSIGNED、UNASSIGNED）。
10. 并联级（DATA_INSUFFICIENT、"组合求解"、不按串联计算）。
11. target_dv=0 被拒绝。
12. rocket_calibration 来源被拒绝。
13. vehicle_mass_t 小于当前级质量被拒绝。
14. 输入 stage / requirement / metadata 不被修改。
15. 返回的新 StageState 与输入不是同一实例（全部路径）。
16. 结果可直接 json.dumps。
17. 大 Δv（耗尽）、小 Δv（1e-6）、边界（required == remaining）浮点情况。
18. data_quality 透传；数据不足时强制 INSUFFICIENT。
19. 纯函数对 detached / parallel / isp=None 的输入抛 ValueError。
20. **stage 类型错误**（dict / None / 字符串）稳定抛 `ValueError`，不泄漏 AttributeError。
21. **容差边界质量守恒**：构造 `required = remaining + 0.75 × MASS_TOLERANCE_T`，结果为 success、`consumed = min(required, remaining)`、`new_remaining = 0`、整箭质量减少量严格等于 consumed、无负推进剂。
22. **极小 target 的 required_propellant_t**：1e-6 / 1e-10 / 1e-12 / 1e-14 全部 > 0 且单调；与独立 `-math.expm1()` 公式一致；普通 400 m/s 结果与旧公式一致。
23. **极小推进剂的 available_delta_v_m_s**：vehicle=100、propellant=1e-15 结果 > 0；与独立 `-math.log1p()` 公式一致；普通质量比结果不变；0 推进剂仍精确返回 0；违反 `0 ≤ fraction < 1`（构造后直改推进剂）抛 ValueError。
24. **极小值反算**：多数量级（1e-6…1e-14）required→available 往返，相对/绝对误差判据。
25. **极小正 Δv 真实燃烧**：target=1e-14 → success、`consumed > 0`、状态推进剂减少量与稳定公式一致、无 NaN/Infinity/负质量；IEEE-754 无法表示 `100 − 3e-16` 的差异时不伪造可观察质量差（1e-10 档验证可观察减少）。

期望值均使用独立公式或手工常数计算，不调用被测函数生成。
