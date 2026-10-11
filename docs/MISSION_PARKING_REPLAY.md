# 串联火箭停泊轨道状态回放（Serial Ascent Parking-Orbit Replay）

阶段 3E 交付物：`mission_parking_replay.py`（项目根目录，纯 Python）。

## 1. 定位与边界

给定一组**已标准化、满推进剂、尚未点火**的串联 `StageState`（从下到上，列表顺序即燃烧顺序），以及一个**外部计算好的"抵达停泊轨道所需有效 Δv"**，从最下面一级开始顺序回放上升段，得到抵达停泊轨道时：

- 哪些级已燃尽并分离。
- 当前仍连接各级的状态。
- 当前活动级及其剩余推进剂。
- 每一级是否已经点火。
- 停泊轨道时的整箭质量。
- 实际完成与仍缺少的 Δv。

```
replay_serial_ascent_to_parking(
    rocket_ref,
    stages,                    # 从下到上的非空 StageState 列表（顺序=燃烧顺序）
    payload_t,
    ascent_required_dv_m_s,    # 外部校准模型给出的有效真空 Δv（含入轨需求与损失）
    data_quality=DataQuality.EXACT,
) -> ParkingOrbitReplayResult
```

依赖：标准库 + `mission_simulation.py` + `mission_solver.py`（`execute_burn`、`MASS_TOLERANCE_T`）。不依赖 Flask / SQLite / `server.models` / 前端 / YAML / .craft-VAB / NumPy。

## 2. ascent_required_dv_m_s 的职责

`ascent_required_dv_m_s` **必须由外部校准模型提供**：

- 已经包含入轨需求与校准损失的有效真空 Δv（例如：独立火箭方程总真空 Δv − 存档校准点 `dv_rem_m_s`）。
- 本函数**不**调用 `compute_orbit_mission` / `compute_rocket`，**不**重新拟合 `loss_model`，也不计算大气损失。

## 3. 输入前置条件（ValueError，不推进任何状态）

- `rocket_ref` 非空字符串。
- `stages` 非空 list/tuple，元素均为 `StageState`；`stage_index` 唯一（允许不连续）。
- `payload_t` 有限非负；`ascent_required_dv_m_s` 有限正数。
- 任一级必须：初始 `attached=True`、`ignition_count=0`（未点火）、**满推进剂**（`propellant_remaining_t ≈ wet_mass_t − dry_mass_t`，用 `MASS_TOLERANCE_T` 判断）。

这些约束表示本函数只负责"从完整串联火箭的起飞状态回放到停泊轨道"，不能接收任意中间飞行状态。

## 4. 预期数据不足（DATA_INSUFFICIENT，非异常）

- 任一级 `parallel=True`。
- 任一级 `isp_vac_s=None`（或不可用）。

返回：`success=False`、`failure_code=DATA_INSUFFICIENT`、`data_quality=INSUFFICIENT`；**不进行任何燃烧或分离**；`achieved_dv_m_s=0`、`remaining_dv_m_s=required_dv_m_s`、`active_stage_index=None`；`stages` 为与输入等值但完全独立的新对象。`failure_reason_zh` 与 `warnings` 明确指出具体 stage_index 与原因。不得把并联级当串联级回放，不得伪造比冲。

## 5. 上升段回放算法

初始整箭质量 = `payload_t + Σ(各级 dry + propellant)`（本阶段不支持额外未建模质量，不擅自增加整流罩/结构质量）。

从列表第一个级开始：

1. 当前级第一次参与燃烧时 `ignition_count` 0 → 1。
2. 用当前整箭质量调用 `execute_burn`（**必须复用，不复制火箭方程**）。
3. 当前级足以完成全部剩余 Δv：扣除实际所需推进剂，停止回放；该级保持 `attached=True` 并成为 `active_stage_index`；上级保持满推进剂、attached、ignition_count=0。**即使当前级恰好燃尽也不自动分离**（没有发生进入下一级的事件）。
4. 当前级不足：`execute_burn` 返回 `PROPELLANT_INSUFFICIENT`（级耗尽），在上升段语义中这是"该级贡献完自己能贡献的全部 Δv"，**不是整次回放失败**：
   - 剩余所需 Δv 扣除该级 `actual_dv_m_s`。
   - **容差内抵达**：若扣除后 `0 < remaining ≤ DV_TOLERANCE_M_S`，整体回放成功——即使底层 BurnResult 为 `PROPELLANT_INSUFFICIENT`，按 Δv 容差解释为成功抵达停泊轨道。此时：`remaining_dv_m_s=0`，当前刚燃烧的级保持 `attached=True` 并成为 `active_stage_index`，**不生成 separation_event**，`continued_to_next_stage=False`，上一级保持未点火，`achieved_dv_m_s` 保留实际完成值（与目标之差在容差内，不伪造）。
   - 若剩余仍 `> DV_TOLERANCE_M_S` 且还有更上面的级：显式分离当前级（扣除干质量，`attached=False`，保留 ignition_count=1、propellant_remaining_t=0 供审计），点燃下一级。
   - 若已是最后一级：整体返回 `PROPELLANT_INSUFFICIENT`；最后一级保持 `attached=True`（不自动丢弃）；`remaining_dv_m_s` 为未完成的正值。
5. 每个物理级本阶段**最多点火一次**。
6. `multi_ignition` 不影响首次上升点火（本阶段不模拟滑行后重启）。
7. 不得自动跳过尚有推进剂的级；不得在未耗尽当前级时提前选择上一级；不得自动分离最终活动级。

## 6. 数值与边界语义

- `DV_TOLERANCE_M_S = 1e-6`：剩余 Δv 在容差内视为完成。
- **容差内抵达不产生额外分离**：级耗尽后若剩余 Δv 已落在容差内，该级保持 attached 并成为活动级；不生成 separation_event，不点燃上一级（即使底层 BurnResult 是 PROPELLANT_INSUFFICIENT，也按回放的 Δv 容差解释为成功抵达；achieved 保留实际值，与目标之差在容差内）。剩余 Δv 大于容差才会分离并进入上一级。
- 不生成负的 `remaining_dv_m_s`。
- 不用人为最小推进剂消耗解决浮点误差；极小但大于容差的 Δv 必须消耗严格正推进剂。
- **每一步**验证质量守恒：`current_vehicle_mass == payload_t + Σ(attached 级当前质量)`；超出 `MASS_TOLERANCE_T` 属于内部实现错误，**明确抛出异常**，不静默修正（`mass_conservation_verified=True`）。
- 推进剂不足级的实际 Δv 采用 `execute_burn` 返回值，不用目标值代替。

## 7. 审计记录

`burn_segments` 每项：`stage_index`、`target_remaining_dv_m_s`（该级点火前整次回放尚需 Δv）、`actual_dv_m_s`、`propellant_consumed_t`、`propellant_remaining_t`、`mass_before_t`、`mass_after_burn_t`、`ignition_count_after`、`exhausted`、`continued_to_next_stage`。

注意：正常耗尽并继续上一级的级，底层 `execute_burn` 虽返回 `PROPELLANT_INSUFFICIENT`，但 `burn_segments` 明确记录为"级能力耗尽后正常进入下一级"（`continued_to_next_stage=True`），不误导为任务终止。

`separation_events` 每项：`stage_index`、`reason='ascent_stage_exhausted'`、`discarded_mass_t`（= 干质量，推进剂已耗尽）、`mass_before_t`、`mass_after_t`。

`metadata`：`stage_order`、`total_stage_count`、`burned_stage_count`、`separated_stage_count`、`mass_conservation_verified`、`replay_model='serial_impulsive_calibrated_dv'`。

## 8. 成功与失败

- **成功**：`success=True`、`failure_code=NONE`、`failure_reason_zh=''`；`achieved_dv_m_s` 在容差内等于 `required_dv_m_s`；`remaining_dv_m_s=0`；`active_stage_index` 为完成入轨的当前连接级。
- **总推进剂不足**：`success=False`、`failure_code=PROPELLANT_INSUFFICIENT`，原因给出目标/实际完成/短缺 Δv；返回真实燃烧后的级状态；已耗尽的最后一级 `attached=True`；不生成不存在的后续级或燃烧。
- **数据不足**：`success=False`、`failure_code=DATA_INSUFFICIENT`；不改任何推进剂/attached/ignition_count；`achieved=0`、`remaining=required`、`active=None`。

## 9. 测试

`tests/test_mission_parking_replay.py`（26 项）：

1-2. 单级部分燃烧成功抵达；单级恰好耗尽时成功且不自动分离。
3. 两级：一级耗尽 → 分离 → 二级部分燃烧。
4. 三级：连续两次分离后由第三级完成。
5. 下级耗尽后才点燃上级（上级 `mass_before` = 分离后质量）。
6-7. 每个实际燃烧级 `ignition_count=1`；未点燃上级 `ignition_count=0` 且推进剂全满。
8. `multi_ignition=False` 不阻止首次点火。
9-10. 总 Δv 不足 → PROPELLANT_INSUFFICIENT；最后一级保持 attached。
11-13. 数据不足零燃烧零分离；parallel / isp=None → DATA_INSUFFICIENT；warning 含 stage_index 与原因。
14. 初始 detached / 已有 ignition_count / 非满推进剂 → ValueError。
15. 非连续 stage_index 按输入列表顺序执行（`stage_order` 保留）。
16-18. 每次燃烧/分离质量守恒；分离只扣干质量；最终质量 = payload + attached 各级当前质量。
19. 极小正 Δv 消耗正推进剂。
20. **容差内抵达**：耗尽后剩余 5e-7 m/s（< 容差）→ 当前级 attached + active、零分离、上级未点火、achieved 为实际值；**略高于容差**（5e-6 m/s）→ 分离并进入上一级。
21-23. 输入完全不变；输出不共享引用；可直接 json.dumps。
24. burn_segments / separation_events 字段完整且顺序正确。
25. 失败前已发生的燃烧与分离状态被保留。

数值期望全部使用独立公式（`-log1p` / `-expm1`）或人工常数计算。

## 10. 集成探针（Neutron I 2630）

读取 `rockets/neutron_i_2630.yaml` → `normalize_stages` 得到两级 → 选 2.5 t / 220 km 校准点（`dv_rem_m_s=1581`）→ 独立火箭方程计算 2.5 t 载荷的总真空 Δv → `ascent_required = 总真空 Δv − 1581` → 回放。验证：成功、第一级耗尽并分离、第二级 active 且 `ignition_count=1` 仍有推进剂、回放后状态独立计算的剩余可用 Δv ≈ 1581 m/s（delta 2.0）。

## 11. 当前仍未实现

- 并联级联合燃烧（回放对 parallel 级返回 DATA_INSUFFICIENT）。
- 大气损失建模与校准点拟合（`ascent_required_dv_m_s` 必须由外部提供）。
- 滑行后重启（`multi_ignition` 只影响后续任务段）。
- 自动执行级分配与分离计划（回放只产生"入轨后状态"，post-parking 任务由 `simulate_multistage_sequence` 显式执行）。
- API / 数据库 / YAML / 前端接入。
