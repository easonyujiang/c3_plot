# 任务仿真数据契约（Mission Simulation Data Contract）

阶段 2 交付物。纯 Python 数据契约模块：`mission_simulation.py`（项目根目录）。

## 1. 目的与边界

为"逐级、多次点火任务仿真"（阶段 3 起实现求解器）定义稳定的数据结构和序列化契约。

本阶段**只定义数据**，不实现：

- 级分配（哪个 stage 执行哪次点火）。
- 火箭方程与推进剂扣除。
- 分级时机。
- 任何 Flask、SQLite、前端或 API 接入。

模块约束：

- 只依赖标准库（`dataclasses`、`enum`、`copy`、`math`、`numbers`）。
- 不 import Flask / SQLite / `server.models` / 前端代码。
- 不读写文件。

## 2. 结构职责

### 2.1 BurnRequirement —— 任务输入（"要求做什么"）

只表示一次燃烧的目标：

| 字段 | 类型 | 说明 |
|---|---|---|
| `burn_id` | str | 稳定标识（如 `park`、`departure`、`circularize`） |
| `order` | int ≥ 1 | 任务内顺序 |
| `name` | str | 燃烧名称，默认 `''` |
| `target_dv_m_s` | float ≥ 0 | 目标 Δv |
| `source` | str | 来源（如 `mission_model`、`rocket_calibration`） |
| `preferred_stage_index` | int ≥ 0 或 None | 建议执行级，可空 |
| `metadata` | dict | 附加信息 |

**明确排除（由求解器决定）**：`is_first_ignition`、`restart_allowed`、实际执行级、实际推进剂消耗、成功/失败状态。同一任务要求既可能是首次点火也可能是重启，取决于级分配结果。

### 2.2 StageState —— 某一时刻的级状态（求解器状态快照）

| 字段 | 类型 | 说明 |
|---|---|---|
| `stage_index` | int ≥ 0 | 级索引（0 = 第一级，最大 = 末级） |
| `name` | str 或 None | 可空 |
| `wet_mass_t` | float ≥ 0 | 湿质量（含推进剂） |
| `dry_mass_t` | float ≥ 0 | 干质量，必须 ≤ wet_mass_t |
| `propellant_remaining_t` | float ≥ 0 | 剩余推进剂，必须 ≤ wet − dry（±1e-9 容差吸收浮点噪声） |
| `isp_vac_s` | float > 0 或 None | 真空比冲，可空（数据不足时） |
| `multi_ignition` | bool | 硬件是否支持重启（静态能力） |
| `ignition_count` | int ≥ 0 | 已点火次数（求解器推进） |
| `attached` | bool | 是否仍与箭体相连 |
| `parallel` | bool | 是否并联助推级 |
| `metadata` | dict | 附加信息 |

`isp_vac_s` 语义：

- `None` = 数据不足（该级尚无可用发动机参数），合法。
- 一旦提供，必须**严格大于 0**；`0`、负数、NaN、Infinity 一律抛 `ValueError`。

本阶段只定义结构，不实现状态推进。

### 2.3 BurnResult —— 单次燃烧结果（求解器输出）

| 字段 | 类型 | 说明 |
|---|---|---|
| `burn_id` / `order` | str / int | 对应 BurnRequirement |
| `stage_index` | int ≥ 0 或 None | 实际执行级；未分配时 None |
| `target_dv_m_s` | float ≥ 0 | 目标（来自任务） |
| `actual_dv_m_s` | float ≥ 0 或 None | 实际提供 Δv；未计算时 None |
| `mass_before_t` / `mass_after_t` | float ≥ 0 或 None | 燃烧前后整箭质量 |
| `propellant_consumed_t` / `propellant_remaining_t` | float ≥ 0 或 None | 消耗/剩余推进剂 |
| `ignition_kind` | IgnitionKind | `first` / `restart` / `unassigned`，**由求解器判定** |
| `success` | bool | 是否满足目标 |
| `failure_code` | FailureCode | 稳定失败码 |
| `failure_reason_zh` | str | 中文说明，可与 failure_code 同时存在（如失败码 + 细节） |
| `data_quality` | DataQuality | 数据置信度 |
| `metadata` | dict | 附加信息 |

None 表示"未计算"，不是非法值；数值一旦给出必须 ≥ 0。

### 2.3.1 BurnResult 一致性约束（创建时强制）

| 规则 | 说明 |
|---|---|
| `success=True` | `failure_code` 必须是 `FailureCode.NONE`。 |
| `success=False` | `failure_code` 不能是 `FailureCode.NONE`；`failure_reason_zh` 必须是非空字符串。 |
| `ignition_kind=FIRST` / `RESTART` | `stage_index` 必须存在（≥ 0）。 |
| `ignition_kind=UNASSIGNED` | `stage_index` 必须为 `None`。 |
| `mass_before_t` 与 `mass_after_t` 都存在 | `mass_after_t` 不能大于 `mass_before_t`。 |
| `actual_dv_m_s` 与 `target_dv_m_s` | **不**强制 actual ≤ target；超额燃烧在数据结构层允许。 |

**校准覆盖燃烧为什么允许 `UNASSIGNED` + `success=True`**：进入停泊轨道的第一次点火由火箭校准曲线覆盖（`compute_orbit_mission` 的 `burn_sequence` 首项，`source='rocket_calibration'`，见 `compute_c3_mass.py:413-430`），不需要（也不允许）分配到某一级再扣一次推进剂。因此"校准覆盖"燃烧是合法组合：`ignition_kind=UNASSIGNED`、`stage_index=None`、`success=True`、`failure_code=NONE`。同样，**不要求所有成功 BurnResult 都带执行级**。

### 2.4 MissionSimulationResult —— 完整任务结果（求解器输出）

| 字段 | 类型 | 说明 |
|---|---|---|
| `rocket_ref` | str | 火箭标识 |
| `payload_t` | float ≥ 0 | 载荷质量 |
| `burns` | list[BurnResult] | 每次燃烧结果（可含未分配项） |
| `success` | bool | 任务整体是否成功 |
| `failure_code` | FailureCode | 整体失败码 |
| `failure_reason_zh` | str | 整体中文说明 |
| `data_quality` | DataQuality | 整体数据置信度 |
| `warnings` | list[str] | 警告列表 |
| `metadata` | dict | 附加信息 |

### 2.4.1 MissionSimulationResult 一致性约束（创建时强制）

| 规则 | 说明 |
|---|---|
| `success=True` | `failure_code` 必须是 `FailureCode.NONE`。 |
| `success=False` | `failure_code` 不能是 `FailureCode.NONE`；`failure_reason_zh` 必须非空。 |
| 不强制"某个 burn 失败 ⇒ mission 失败" | 该汇总逻辑属于后续求解器。 |
| 不自动修改 | 调用方传入的 `success` / `failure_code` 只被校验，不被静默改写。 |

## 3. 枚举稳定性要求

- 枚举值为**稳定的小写字符串**，属于未来 API 契约的一部分。**禁止改名、改词**；只允许追加新成员。
- 序列化时输出 `value` 字符串（如 `'propellant_insufficient'`），不得输出 Python 枚举对象。

| 枚举 | 值 |
|---|---|
| `FailureCode` | `none` · `invalid_input` · `data_insufficient` · `no_stage_assigned` · `restart_unsupported` · `propellant_insufficient` |
| `DataQuality` | `exact` · `estimated` · `insufficient` |
| `IgnitionKind` | `first` · `restart` · `unassigned` |

## 4. 序列化与不可变性

- 每个结构提供 `to_dict()`；另有模块级 `to_dict(value)` 可转换任意嵌套结构。
- `to_dict()` 输出可直接 `json.dumps()`；嵌套 `burns`、`warnings`、`metadata`、枚举全部转换为普通 dict/list/str。
- 每个结构提供 `from_dict(cls, data)` 兼容普通 dict 输入；**未知字段显式抛 ValueError**，不静默忽略。
- 构造时对调用方传入的 `metadata`/`warnings` 深拷贝；修改序列化结果不会反向影响原对象。
- 非法输入（负质量、负 Δv、非正 order、dry > wet、推进剂超容量、未知枚举、空 burn_id、一致性约束）在创建时抛 `ValueError`，**不静默修正**。

### 4.1 metadata 的递归 JSON 限制

`metadata`（以及 `to_dict()` 触达的任何 dict/list/tuple）只允许以下类型：

- `None`
- `bool`
- `str`
- `int`
- **有限** `float`（`NaN`、`Infinity`、`-Infinity` 拒绝）
- `list`
- `tuple`
- `dict`（键必须是字符串）

约束与行为：

1. dict 键必须是字符串；非字符串键抛 `ValueError`（如 `metadata keys must be strings`）。
2. `tuple` 在保存与序列化时**规范化为 `list`**。
3. `set`、`bytes`、`Path`、任意 class 实例等抛 `ValueError`，错误信息带字段路径，例如：`metadata.engine.value is not JSON-compatible`、`metadata.parts[1] is not JSON-compatible`、`metadata.x must be a finite number`。
4. 不使用 `default=str` 之类的兜底转换——未知对象一律拒绝。

### 4.2 to_dict() 的最终检查

数据结构只保证**构造时**有效。如果调用方随后直接修改公开字段（例如 `req.metadata['bad'] = object()`），`to_dict()` 会在序列化时对 JSON 不兼容数据进行**最后检查并抛 ValueError**（同样带字段路径），不会把问题拖到 `json.dumps()` 才暴露。

## 5. JSON 示例

```json
{
  "rocket_ref": "Neutron I 2630",
  "payload_t": 1.0,
  "success": false,
  "failure_code": "propellant_insufficient",
  "failure_reason_zh": "推进剂不足",
  "data_quality": "estimated",
  "burns": [
    {
      "burn_id": "park",
      "order": 1,
      "target_dv_m_s": 0.0,
      "ignition_kind": "unassigned",
      "success": true,
      "failure_code": "none",
      "failure_reason_zh": "",
      "data_quality": "exact",
      "actual_dv_m_s": null,
      "stage_index": null,
      "mass_before_t": null,
      "mass_after_t": null,
      "propellant_consumed_t": null,
      "propellant_remaining_t": null,
      "metadata": {}
    },
    {
      "burn_id": "departure",
      "order": 2,
      "target_dv_m_s": 2451.0,
      "ignition_kind": "first",
      "success": false,
      "failure_code": "propellant_insufficient",
      "failure_reason_zh": "推进剂不足",
      "data_quality": "estimated",
      "actual_dv_m_s": 2000.0,
      "stage_index": 1,
      "mass_before_t": 20.0,
      "mass_after_t": 12.0,
      "propellant_consumed_t": 8.0,
      "propellant_remaining_t": 10.0,
      "metadata": {}
    }
  ],
  "warnings": ["w1", "w2"],
  "metadata": {"mods": ["SSTU"]}
}
```

对应 BurnRequirement 示例（任务输入）：

```json
{
  "burn_id": "departure",
  "order": 2,
  "name": "霍曼转移出发点火",
  "target_dv_m_s": 2451.0,
  "source": "mission_model",
  "preferred_stage_index": null,
  "metadata": {}
}
```

## 6. 测试

`tests/test_mission_simulation.py`（阶段 2.1 加固后 43 项）：

1. BurnRequirement 不包含求解结果字段；from_dict 拒绝未知字段。
2. 四种结构均可转 JSON（to_dict → json.dumps → 可解析）。
3. 枚举序列化为稳定小写字符串（非枚举对象）。
4. BurnResult 区分 first / restart / unassigned。
5. failure_code 与中文说明同时存在。
6. 嵌套 MissionSimulationResult 正确序列化；from_dict 往返一致。
7. 修改序列化结果不影响原对象 metadata / warnings。
8. 构造时拷贝调用方 metadata。
9. 负质量、负 Δv、非法 order、dry > wet、推进剂超容量、未知枚举、空字符串等非法输入抛 ValueError。
10. **metadata 递归 JSON 限制**：顶层 object / 嵌套 object（字段路径 `metadata.engine.value`）/ 列表元素 object（`metadata.parts[1]`）/ set / bytes / NaN / Infinity / 非字符串键均抛 ValueError；tuple 规范化为 list；合法 JSON 类型通过。
11. **to_dict() 最终检查**：构造后直接向公开字段写入非法对象，`to_dict()` 抛 ValueError（含 `burns[0].metadata.x` 路径）。
12. **BurnResult 一致性**：success↔failure_code 双向约束；失败必须带中文原因；FIRST/RESTART 必须带 stage_index；UNASSIGNED 必须无 stage_index；mass_after > mass_before 拒绝；actual > target 允许（超额燃烧）。
13. **合法组合**：校准覆盖燃烧（UNASSIGNED + success + NONE）、成功首次点火、成功重启、未分配失败均合法。
14. **MissionSimulationResult 一致性**：success↔failure_code / reason 约束；不自动从 burns 推导 success（不改写调用方值）。
15. **StageState.isp_vac_s**：None 合法；0 / 负数 / NaN / Infinity 拒绝；正数通过。

## 7. 后续阶段（未实现，见 docs/AI_STAGED_IMPLEMENTATION_PLAN.md）

- 阶段 3：逐级燃烧求解器 —— 使用本契约，每次燃烧以火箭方程扣除推进剂，推进 StageState.ignition_count / attached / propellant_remaining_t，产生 BurnResult。
- 阶段 4：接入 /api/mission-query —— 保留现有字段，只新增本契约字段。
- 本阶段禁止事项均已遵守：未修改 compute_orbit_mission、find_rockets_for_orbit、数据库、迁移、dashboard 文件；未修复 SSTU Double 与 draftToYaml；未实现 normalize_stages 与燃烧算法；未新增第三方依赖；未改变现有枚举字符串。
