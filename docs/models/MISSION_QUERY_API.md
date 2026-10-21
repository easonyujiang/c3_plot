# /api/mission-query 详细任务验证 API（阶段 4B）

本阶段把阶段 4A 的分析服务接入 `POST /api/mission-query`，在保留旧快速估算字段的同时，对每枚火箭返回详细验证结果；最终推荐火箭以详细验证为依据。

## 1. 请求参数

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `target_altitude_km` | number | 35786 | ≥ 80 |
| `inclination_deg` | number | 180 | 0–180 |
| `parking_alt_km` | number | 220 | ≥ 80 |
| `launch_lat` | number | 19.81 | -90–90 |
| `payload_t` | number | 0 | ≥ 0；0 表示未指定载荷 |
| `orbit_type` | string | `circular` | `circular` / `transfer` |
| `arrival_strategy` | string | `combined` | **新增**：`combined`（圆轨与倾角变化合并点火）/ `separate`（独立点火） |
| `include_audit` | bool | false | **新增**：true 时附加完整 audit 数据 |

严格校验（HTTP 400 + 稳定 `{"error": ...}`）：

- 数值字段必须是有限实数；`true`/`false` 当作 1/0、NaN、±Infinity、无法解析的字符串一律拒绝。
- 未知参数（含 `max_search_nodes`、`initial_samples` 等内部资源控制参数）直接 400——内部固定使用 `payload_tolerance_t=0.001`、`initial_samples=17`、`max_evaluations=128`、`max_search_nodes=10000`。
- 非 JSON 对象、数组、空请求体 → 400。

## 2. arrival_strategy

原样传给 `compute_orbit_mission`：

- `combined`：到达远地点后，圆轨化与倾角变化按一次合并点火计算。
- `separate`：圆轨化与倾角变化作为独立点火计算（产生更多点火次数）。

`mission` 返回中保留实际生成的点火序列（停泊轨道插入 → 转移轨道点火 → 远地点圆轨 → 必要时的倾角变化），多点火与级间分离约束由详细验证管线处理；规划候选或未执行燃烧**不会**被伪装成已执行时间线。

## 3. 旧快速估算与详细验证的区别

- **旧快速估算**：`models.find_rockets_for_orbit` —— dv_curve 反插值 + 末级 `multi_ignition` 布尔筛选。快，但只是一阶近似。
- **详细验证**：`analyze_rocket_for_mission` —— 校准插值 → 串联总 Δv → 停泊轨道回放 → 任务级规划（`whole_burn_per_stage`）→ `simulate_multistage_sequence` 计划验证。

两者在响应中**共存**：

- 旧字段（`capacity`、`feasible`、`feasible_count` 等）含义不变，仍表示快速估算。
- 每个条目新增 `quick_estimate`（旧字段快照）、`detailed_validation`（`to_api_dict(...)` 结果）与顶层别名 `verification_status` / `verified_feasible` / `verified_capacity` / `recommendation_eligible` / `detailed_reason`。
- `feasible` 与 `verified_feasible` 暂时同时存在：旧前端继续读旧字段；新前端可渐进迁移到详细字段。

## 4. 三态 verification_status

| 状态 | 含义 |
|---|---|
| `verified_feasible` | 详细验证确认可行（有载荷：单载荷评估可行；无载荷：容量搜索 bounded）。 |
| `verified_infeasible` | 明确的物理/任务失败（failure_code != DATA_INSUFFICIENT）。 |
| `data_insufficient` | 并联级、缺失发动机性能/配置/计算结果、dv_curve 缺失或非法、校准范围外、容量边界未封闭、搜索上限等。 |

`verified_feasible` 为 `null`（数据不足）或 `false`（明确失败）时 `recommendation_eligible=false`。

## 5. 推荐资格规则

`recommended` **只从 `recommendation_eligible == true` 的详细验证结果中选择**；旧快速估算只作排序辅助，不作可行性权威。

- **有指定载荷**：只考虑详细验证可行者；优先选择 `verified_capacity − payload` 非负余量最小者；无可比 capacity 的火箭以旧 `margin` 为次级排序依据；之后按旧 `capacity` 排序。
- **无指定载荷**：只考虑 bounded 且 eligible 者；按 `verified_capacity` 从高到低。
- 没有任何详细验证合格候选 → `recommended = null`（即使旧快速估算声称可行）。

`recommended` 保留旧前端读取的字段（rocket、slug、capacity、margin 等）并包含新增详细字段。

## 6. 顶层详细分析摘要

```json
"detailed_analysis": {
  "enabled": true,
  "model": "calibrated_serial_multiburn",
  "verified_feasible_count": 1,
  "verified_infeasible_count": 0,
  "data_insufficient_count": 0,
  "recommendation_basis": "detailed_validation",
  "include_audit": false
}
```

`feasible_count` 保留旧快速估算含义；详细计数用新增字段表达。

## 7. 单火箭错误隔离

- 某枚火箭配置/计算数据缺失 → 生成统一的 `data_insufficient` 详细结果（`server/mission_analysis.py::unavailable_rocket_analysis`），不影响其他火箭。
- 预期数据问题（`ValueError`）按火箭隔离。
- **RuntimeError、断言失败、内部不变量损坏等意外错误继续传播为服务器错误**，不伪装成数据不足。

## 7.1 点火时间线语义（前端展示约定）

`burn_timeline` 每项按 `source` 区分语义，前端必须按此显示：

- **`source = "rocket_calibration"`（性能曲线标定）**：该段性能已由 dv_curve 校准数据覆盖，**不是逐级仿真实际执行的一次燃烧**。即使 `success=true`，含义是"该要求已由标定覆盖"，不是发动机执行成功。显示为：类型「性能曲线标定」、执行级「不适用」、点火类型「非实际点火」、结果「由校准曲线覆盖」；实际 Δv 与质量变化显示「由标定曲线覆盖」/「—」，**不得伪造质量和推进剂消耗**。
- **`success = null`（未执行）**：执行级「未分配」、点火类型「未执行」、结果「未执行」；实际 Δv、质量、推进剂均显示「—」。
- **`success = true` 且 `source = "mission_model"`（真实任务燃烧）**：显示实际执行级、首次点火/重启、成功，以及实际 Δv、燃烧前后质量与推进剂消耗。
- **`success = false`**：显示已尝试的执行级（无级时「未分配」）、点火类型、失败与稳定 `failure_code`。

枚举翻译：数据质量 `exact→精确输入 / estimated→估算 / insufficient→数据不足`；点火类型 `first→首次点火 / restart→重启 / unassigned→未分配`；未知值安全回退到原字符串。失败码保留稳定英文码，同时展示中文 `failure_reason_zh` 或 `detailed_reason`。

## 8. 默认紧凑响应与 include_audit

- 默认（`include_audit=false`）响应只含紧凑摘要（容量搜索只带 `evaluation_count`/`boundary_width_t` 等元数据，不含全部采样轨迹与级状态）。
- `include_audit=true` 时每个条目的 `detailed_validation.audit` 附加完整审计（normalization、burn_conversion、capacity_search、requested_payload_evaluation、metadata）——响应体积约放大 7 倍（Neutron 单火箭实测约 9 KB → 67 KB）。

## 9. 典型请求与响应示例

请求（0.2 t，GEO 逆行）：

```json
POST /api/mission-query
{
  "target_altitude_km": 35786,
  "inclination_deg": 180,
  "arrival_strategy": "combined",
  "payload_t": 0.2
}
```

响应骨架：

```json
{
  "mission": { "...": "compute_orbit_mission 完整结果（含 burn_sequence）" },
  "payload_t": 0.2,
  "count": 1,
  "feasible_count": 0,
  "recommended": { "rocket": "Neutron I 2630", "slug": "neutron_i_2630",
                   "...": "旧字段 + 新详细字段" },
  "results": [
    {
      "rocket": "Neutron I 2630", "slug": "neutron_i_2630",
      "capacity": 0.1, "feasible": false, "...": "旧快速估算字段",
      "quick_estimate": { "...": "旧字段快照" },
      "detailed_validation": { "verification_status": "verified_feasible",
                               "capacity": { "status": "bounded",
                                             "max_payload_t": 0.5022,
                                             "boundary_width_t": 0.0006,
                                             "evaluation_count": 26 },
                               "requested_payload": { "payload_t": 0.2,
                                                      "feasible": true,
                                                      "plan_verified": true },
                               "burn_timeline": [ "...": "真实仿真时间线" ],
                               "model": { "...": "模型元数据" } },
      "verification_status": "verified_feasible",
      "verified_feasible": true,
      "verified_capacity": 0.5022252075195314,
      "recommendation_eligible": true,
      "detailed_reason": ""
    }
  ],
  "detailed_analysis": { "enabled": true, "model": "calibrated_serial_multiburn",
                         "verified_feasible_count": 1,
                         "verified_infeasible_count": 0,
                         "data_insufficient_count": 0,
                         "recommendation_basis": "detailed_validation",
                         "include_audit": false }
}
```

## 10. Neutron I 2630 实测结果

- **无载荷**：`verified_feasible`、`verified_capacity ≈ 0.5022 t`（boundary 0.0006 ≤ 0.001）、`recommended = neutron_i_2630`。
- **0.2 t**：`verified_feasible`、requested.feasible=true、plan_verified=true、时间线两次 RESTART（第二级）、`recommended` 正常。
- **2.5 t**：`verified_infeasible`、`recommended = null`、detailed_reason 指明 `orbit_burn_2` 目标 2450.7 m/s 任务级规划失败；容量搜索仍独立 bounded ≈ 0.502 t。

## 11. 测试

`tests/test_mission_query_api.py`（16 项）：

1-7. 旧字段保留+新字段存在；recommended 只来自详细验证（快速可行+详细不可行不推荐）；单火箭 data_insufficient 隔离；全部不可用 recommended=null；有/无载荷查询。
8. combined/separate 正确传入轨道计算。
9. include_audit 默认关闭、显式开启后出现完整审计。
10-11. 非法 JSON/参数 400；bool、NaN、Infinity、非法字符串、未知参数拒绝。
12. RuntimeError 不被错误包装为 data_insufficient。
13. 默认紧凑响应不含大型搜索轨迹。
14-16. Neutron 真实服务集成（无载荷 ≈0.502 bounded / 0.2 t verified feasible / 2.5 t verified infeasible，不访问真实数据库）。

## 12. 仍留给下一阶段

- 前端迁移到 `verified_feasible` / `detailed_validation` 字段。
- `detailed_analysis` 用于全库火箭列表/比较视图。
- 跨火箭任务分配、新的轨道优化器、并联燃烧与单次燃烧跨级模型。
