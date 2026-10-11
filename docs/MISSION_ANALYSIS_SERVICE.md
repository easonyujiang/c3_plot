# 单火箭轨道任务后端分析服务（Mission Analysis Service）

阶段 4A 交付物：`server/mission_analysis.py`。

## 1. 定位与边界

与 Flask、SQLite 解耦的单火箭分析服务。输入一枚火箭的原始配置、计算数据、轨道任务与可选载荷，输出紧凑、稳定的 API 摘要 + 完整审计对象。

```
analyze_rocket_for_mission(
    rocket, slug, config, computed_data, mission,
    payload_t=None, data_quality=DataQuality.EXACT,
    payload_tolerance_t=0.001, monotonicity_samples=17,
    max_evaluations=128, max_search_nodes=10000,
) -> RocketMissionAnalysisResult
```

服务内部组合：

1. `normalize_stages`（原始配置级 → StageState）。
2. `burn_sequence_to_requirements`（burn_sequence → BurnRequirement）。
3. `find_max_payload_for_mission`（容量搜索）。
4. 用户提供 `payload_t` 时，再调用 `evaluate_payload_for_mission`。

本模块不依赖 Flask / request / jsonify / SQLite / `server.models` / 前端；自身不写数据库或文件（`normalize_stages` 间接只读发动机 .cfg 是允许的）。**不修改 `/api/mission-query` 或任何现有路由。**

## 2. 为什么容量搜索与指定载荷评估是两个独立结论

- **容量搜索**回答"校准范围内最大可验证载荷是多少"；若边界落在校准范围外（below/above_calibration_range）或搜索因数据不足中断，它无法给出精确最大值——但这**不代表**某个明确处于校准范围内的载荷不可评估。
- **指定载荷评估**回答"这一个载荷可行吗"，能给出确定的可行/不可行或数据不足结论。
- 因此即使容量边界未封闭，只要基础数据适合评估，服务仍单独运行单载荷评估；**不得用容量搜索失败直接替代单载荷评估**。

权威来源：

- 有用户载荷 → `requested_payload_evaluation` 为权威。
- 无用户载荷 → `capacity_search` 为权威。

## 3. verification_status 三态语义

| 状态 | 含义 |
|---|---|
| `verified_feasible` | 有载荷：单载荷评估 feasible=True；无载荷：容量 bounded。 |
| `verified_infeasible` | **只用于明确的物理/任务失败**：有载荷且失败代码 != DATA_INSUFFICIENT。数据不足不得当作已验证不可行。 |
| `data_insufficient` | 并联级、缺失发动机性能、缺失/非法 dv_curve、校准范围外、容量边界未封闭（below/above/data_insufficient/non_monotonic/evaluation_limit）、或任一阶段 DATA_INSUFFICIENT。 |

## 4. recommendation_eligible 的保守规则

只有 `verification_status='verified_feasible'` 时为 True。数据不足与明确失败均不得推荐。

## 5. 错误隔离（全库循环安全）

- **调用错误**（ValueError，调用方传参错误）：rocket/slug/config/computed_data/mission 类型错误、config 缺 stages、mission 缺 burn_sequence、payload_t 非法（0/负数/NaN/bool）、搜索参数非法。
- **单枚火箭数据问题**（转换为自身的 `data_insufficient` 结果，不中断全库分析）：computed_data 缺 dv_curve、dv_curve 非法或非单调、发动机文件缺失、并联级暂不支持、校准与级性能不一致、搜索返回数据不足。
- **不吞掉**：编程错误、`RuntimeError` 质量守恒失败、计划/仿真内部不一致——继续暴露供测试发现。只捕获预期的 `ValueError`，不用裸 `except Exception`。

失败阶段 `failure_phase` 取值：`normalization` / `burn_conversion` / `capacity_search` / `calibration_lookup` / `parking_replay` / `parking_consistency` / `stage_planning` / `plan_verification` / None。有载荷时优先使用单载荷评估的失败阶段。

## 6. 默认 API 摘要 vs 完整 audit

`to_api_dict(include_audit=False)`（默认）：

- **不含**：capacity_search.evaluations 全部采样、每个搜索点的完整停车回放、完整级状态、重复嵌套输入配置、完整 normalization metadata、搜索缓存。
- **包含**：rocket / slug / verification_status / verified_feasible / recommendation_eligible / data_quality / failure_code / failure_reason_zh / failure_phase / capacity（status、max_payload_t、feasible_lower_bound_t、infeasible_upper_bound_t、payload_tolerance_t、data_quality、evaluation_count、boundary_width_t）/ requested_payload / burn_timeline / warnings / model。

`to_api_dict(include_audit=True)` 增加 `audit`：normalization、burn_conversion、capacity_search、requested_payload_evaluation 的完整 to_dict 与 metadata。`to_dict()` 返回完整结构供测试与后端诊断。

**为什么不能把内部完整搜索结果默认发给前端**：一次容量搜索含 17+ 次单载荷评估，每次评估又含停车回放与完整级状态——直接下发会放大几十倍响应体积，且暴露内部搜索路径细节；默认摘要（Neutron 案例实测约 2.3 KB）比完整 audit（约 30.6 KB）小一个数量级以上。摘要与完整结果互不共享嵌套可变引用。

## 7. 时间线构造规则

- 来源选择：有载荷 → requested_payload_evaluation；无载荷且容量 bounded → capacity_search.best_feasible_evaluation；都没有成功仿真 → 用 burn_conversion.requirements 生成未分配时间线。
- **存在 simulation**：严格按 simulation.burns 顺序；`source='rocket_calibration'` 的条目是**性能标定覆盖，不是实际点火**——显示为"由校准曲线覆盖"（`success=true` 只表示该要求已由标定覆盖，不是发动机执行成功），`stage_index=None`、`ignition_kind='unassigned'`、不消耗推进剂；`mission_model` 条目才是任务后续真实燃烧，显示 FIRST/RESTART、执行级、质量与推进剂消耗。
- **规划失败 / simulation=None**：已成功停车不等于任务燃烧已执行；时间线使用原 requirements，**不得把 stage_plan 搜索中测试过的候选级当作已执行级**；所有未执行项 `success=None`、`stage_index=None`、`ignition_kind=None`；失败燃烧在 failure_reason_zh 中说明。

## 8. 数据质量与警告

- 服务结果 data_quality：有载荷 → requested_payload_evaluation.data_quality；无载荷且 bounded → capacity_search.data_quality；其他 → INSUFFICIENT。
- warnings 合并 normalization / burn_conversion / capacity_search / requested_payload_evaluation 的警告：**去重并保持首次出现顺序**（不用 set，保证顺序确定），全部为非空字符串，相同输入重复分析顺序完全一致。

## 9. 测试

`tests/test_mission_analysis_service.py`（34 项）：

1-7. 状态判定：无载荷 bounded；有载荷可行/明确失败；数据不足 verified=None；recommendation_eligible 保守规则；有载荷以单载荷评估为权威（容量 evaluation_limit 不影响）；无载荷以容量为权威。
8-11. 隔离：并联级停止后续阶段；发动机缺失；computed_data 缺 dv_curve；dv_curve 非单调。
12-14. 调用校验：顶层格式 ValueError；payload_t=0/负数/NaN/bool 拒绝；搜索参数原样传递。
15. warnings 去重且顺序稳定。
16-19. API 摘要：默认不含完整 evaluations；include_audit 含完整审计；默认摘要显著小于审计；摘要与完整结果不共享引用。
20-24. 时间线：成功用 simulation.burns；calibration UNASSIGNED；真实燃烧 FIRST/RESTART + stage_index；规划失败不伪造执行级；无仿真用 requirements。
25-27. 输入完全不变；全部结果可 json.dumps；重复分析一致。
28. RuntimeError 不被服务层吞掉。
29. 单枚火箭数据 ValueError 转换为自身 data_insufficient 结果。
30-34. Neutron I 2630 集成（无载荷 / 0.2 t / 2.5 t）。

## 10. Neutron I 2630 集成结果（实测）

- **无载荷**：verified_feasible、eligible=True、capacity bounded（0.5022 / 0.5028，宽度 0.0006，26 次评估）；时间线来自最佳可行边界真实仿真：两次真实燃烧均为第二级 RESTART。
- **0.2 t**：verified_feasible、requested.feasible=True、plan_verified=True、停泊活动级=第二级、时间线两次 RESTART（第二级）。
- **2.5 t**：verified_infeasible、PROPELLANT_INSUFFICIENT、phase='stage_planning'、requested.feasible=False、时间线全部未执行（不伪造执行级）；容量搜索仍独立返回 bounded ≈ 0.502 t。
- 大小：默认 API 摘要 2297 B vs include_audit 30608 B vs to_dict 28755 B。

## 11. 当前仍未接入

- Flask 路由——已接入：`POST /api/mission-query` 现在对每枚火箭调用本服务并返回详细验证（见 `docs/MISSION_QUERY_API.md`）。
- SQLite / 数据库 / YAML / 前端。
- 并联燃烧、单次燃烧跨级、有限推力、级寿命。
