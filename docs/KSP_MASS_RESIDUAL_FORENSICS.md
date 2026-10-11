# KSP 质量残差取证（阶段 6B.3C.1 / 返工 6B.3C.1-R）

只读、可证伪的残差来源调查。本阶段**不**把任何候选公式用于正式质量估算，
也不修改 reconciliation / mass evidence 的正式行为。

## 完整性门：完整候选值与已知部分和（6B.3C.1-R）

部分 PDC 证据**绝不**冒充完整修正：

- `pdc_expected_instance_count` / `pdc_evidence_instance_count` /
  `pdc_valid_candidate_count` / `pdc_candidate_complete` /
  `pdc_missing_or_invalid_instances`；
- 只有所有预期 PDC 都有**唯一、合法**候选质量与合法 current 估计时，
  `pdc_candidate_mass_total_t` / `pdc_candidate_diff_total_t` /
  `residual_after_pdc_t` 才非 null；
- 证据不完整时完整结果全部为 null（不是 0、不是部分和），部分和只进入
  `known_pdc_candidate_mass_subtotal_t` /
  `known_pdc_candidate_diff_subtotal_t`（无任何合法项时为 null）。

## 一一对应要求

PDC 部件与质量证据按 craft_index 一一对应：

- 缺失 evidence → `missing_evidence`；
- 重复 evidence index → `duplicate_evidence_index`（不选择任何一条）；
- evidence 中出现 craft 不存在的 index / PART 自身重复 index →
  `duplicate_craft_part_index` 等诊断；
- PDC PART 与 evidence 身份不一致 → `evidence_identity_mismatch`；
- 任一异常 → 候选不完整，不计算完整 PDC 修正。
  复用 reconciliation 的守恒思想，但不修改其正式行为。

## 数值边界

- bool、NaN、Infinity、负质量一律拒绝；数字字符串允许（解析后使用
  规范化有限浮点，`_finite(current)` 之后的操作全部基于浮点值）；
- `massPerCubicMeter`：唯一 → 采用；多条数值完全一致 → 采用（保留来源
  数量，`mass_per_cubic_meter_resolved`）；冲突 →
  `ambiguous_mass_per_cubic_meter`（不默默选第一条，候选不完整）；
- 非法 residual → `invalid_residual`，`residual_after_pdc_t` 为 null。

## 空样本不能产生支持性结论

- 空 fleet → `pdc_hypothesis_status = insufficient_evidence` +
  `empty_fleet` 诊断（`all([])` 永不产生真值结论）；
- 没有有效普通载具、或任何普通载具 PDC 证据不完整 → 同样
  `insufficient_evidence`。

## 共线与"缺乏特征变化"的区别

共线判断使用**完整计数向量**（不是"是否大于零"），
`collinearity_status`：

| 状态 | 条件 |
| --- | --- |
| `perfect` | 两列计数逐项相同，且样本同时存在 0 与非 0 |
| `not_collinear` | 两列计数不一致 |
| `insufficient_variation` | 全 0 或全非 0（无变化）或空样本——不是有效共线证据 |
| `invalid_evidence` | 计数缺失/非法 |

保留的布尔字段 `colinear_parachute_mk1pod` 只在 `perfect` 时为 true。

## JSON 稳定指纹契约

取证报告只保留稳定指纹字段：`schema_version`、`source_kind`、
`config_cache_path`、`file_size`、`mtime_ns`、`sha256`、
`patched_node_count`；**不**含 `built_at`。CLI `--json` 模式：
stdout 只输出 JSON（显式 UTF-8，中文文件名正确保留），错误与提示写
stderr；相同输入两次运行字节级一致（已验证）。

## CLI 错误契约

`audit_ksp_mass_residuals.py`：文件读取、craft 解析、loadmeta 解析、
索引构建、对账、取证任一失败 → stderr 输出带文件上下文的简洁错误，
退出码 **2**（绝不吞掉错误伪造成功报告）；非法路径/空目录同样退出 2。

## 数据来源

| 证据 | 来源 |
| --- | --- |
| PDC/降落伞实例状态（diameter/height/thickness、caseMass、CHUTE 行） | `craft_draft.parts[].module_evidence`（vab_parser，raw + 严格解析并存，缺失保持缺失，bool/NaN/∞/非法字符串 → None） |
| 模块配置证据（massPerCubicMeter、尺寸上下限、PhysicsSignificance） | 索引 PartRecord `module_evidence`（ksp_install_index，只提取已知相关模块） |
| 逐部件当前估计 | `mass_evidence.instances.current_part_mass_estimate_t` |
| 整箭残差 | `reconciliation`（residual_include_all_t） |

## 候选公式（仅取证）

SSTUProceduralDecoupler 环形壳体：

```text
R = diameter / 2
r = max(R - thickness, 0)
volume = π × (R² − r²) × height
candidate_mass_t = volume × massPerCubicMeter
```

默认配置（diameter=1.25, height=0.1, thickness=0.2, massPerCubicMeter=0.4）
得到 `candidate = 0.026389378290154267 t`。严格校验：所有输入必须有限；
diameter/height > 0；thickness、massPerCubicMeter ≥ 0；thickness ≤ 外半径；
证据不完整 → `insufficient_evidence`，绝不回退常数。计算只用实际
craft/config 字段，**禁止**按部件名硬编码 0.0736106217。

## 真实载具验证结果（20 个 VAB craft，只读）

每个 PDC 实例计算 `diff = current_part_mass_estimate_t − candidate_mass_t`
（实际字段），41 个实例的 diff 全部恒等于 ~0.0736106 t（±1e-7，来自
`0.1 base + modMass − candidate`，其中 modMass 与实例几何精确对应）。
逐载具扣除后：

- 17 个无 `parachuteSingle` 的载具：**16/17** 残差绝对值 ≤ 5e-5 t；
- **Neutrons II 354S.craft** 超差 **−8.0472788e-5 t**（3 个 PDC 的 diff
  与其他载具完全一致，超差来自该载具 residual 本身的 ~8e-5 差异，与 PDC
  公式无关）——**仍是尚未解释的真实反例**，不得通过放宽容差使其通过；
- 3 个特殊载具（1_2T / 1_5T / 2_5T Test.craft）扣除后残差：
  +0.074986288 / +0.074991061 / +0.074985517 t —— 全部在 `0.075 ± 5e-5` 内；
- `parachuteSingle` 与 `mk1pod.v2` 完全共线（`collinearity_status =
  perfect`）。

## 取证容差与正式对账容差的区别

- 正式 reconciliation：`1e-6 t` 绝对 + `1e-7` 相对（不受本阶段影响）；
- 取证比较：`FORENSIC_ABS_TOLERANCE_T = 5e-5 t`，只用于判断假设是否与
  `.loadmeta` 序列化精度吻合，**不改变**正式对账的任何判定。

## PDC 结论的证据强度

- **强证据（逐实例层）**：41/41 实例满足 `current − candidate ≈ 0.0736106`
  恒定（与 modMass 分解互证）；
- **整箭层**：16/17 无伞载具扣除后 ≤ 5e-5，1 例（Neutrons II 354S）
  超差 8.05e-5；
- `pdc_hypothesis_status = insufficient_evidence`（如实判定）：公式被
  大部分证据支持，但仍不能确认为 KSP/SSTU 官方结算公式。

## mk1pod/parachute 完全共线，现阶段不可识别

三份特殊载具的剩余 ~0.075 t 可能来自 `parachuteSingle`（RealChuteModule
caseMass=0.075 配置与真实剩余高度吻合）、`mk1pod.v2` 或两者组合。由于两个
特征完全共线，任何回归都无法拆分贡献——强制拆分属于无约束曲线拟合。
`remaining_residual_hypothesis_status = insufficient_evidence`。

## 下一阶段 6B.3C.2（未实现）

- **6B.3C.2A**：Neutrons II 354S 数值精度与求和顺序取证（解释 8.05e-5
  载具级差异）；
- 受控微型 craft 区分剩余 0.075 t 的真实来源。

