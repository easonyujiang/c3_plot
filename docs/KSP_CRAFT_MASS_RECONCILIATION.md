# 整箭质量对账（阶段 6B.3B / 返工 6B.3B-R / 6B.3B-R2）

对账层把逐 PART 质量证据（6B.3A）的总和与 `.loadmeta.totalMass` 比较，
**准确报告残差与证据缺口**，不自动修正、不分摊、不填充级段质量。

## craft PART 与 evidence 的一一对应规则（6B.3B-R2）

按 `craft_index` 显式建立 `craft_part_by_index` 与
`evidence_records_by_index`，对每个 craft PART 分类：

| 情形 | 处理 |
| --- | --- |
| 恰好一条 evidence 且完全有效 | `valid`，计入候选总质量 |
| 没有任何 evidence | 生成**合成缺失记录**（`synthetic=true`，`invalid_reasons=["missing_craft_instance"]`，保留 craft 的 part/base_part/persistent_id 身份字段；match/resolution/质量/part_source 全 null） |
| 一条 evidence 但状态或质量无效 | `invalid_evidence_instance`（`synthetic=false`，保留原字段 + 具体 invalid_reasons） |
| 同一 index 多条 evidence | `duplicate_craft_index`，**不选择任何一条**，全部记录进 unresolved |
| evidence 中不存在的 craft index | `unexpected_evidence_instance`（`synthetic=false`，最后追加） |

输出顺序稳定：按 craft PART 原始顺序列出缺失/无效/重复，最后追加
unexpected；同类重复按原 evidence 顺序。

## 每个数量字段的精确定义

| 字段 | 语义 |
| --- | --- |
| `expected_instance_count` | `source.part_count`（正整数），否则 `len(craft.parts)`；两者不一致报 `input_structure_mismatch` |
| `evidence_instance_count` | mass_evidence 记录总数 |
| `valid_instance_count` | craft PART 中恰好一条且完全有效的 evidence 数 |
| `invalid_evidence_instance_count` | 对应某 craft PART 但内容无效的 evidence 记录数（不含合成缺失） |
| `missing_craft_instance_count` | 完全没有 evidence 的 craft PART 数 |
| `unexpected_evidence_instance_count` | evidence 中找不到对应 craft PART 的记录数 |
| `duplicate_evidence_instance_count` | 因同一 craft_index 重复而无法唯一对应的 evidence 记录数 |
| `unresolved_instance_count` | `unresolved_instance_evidence` 的实际长度 |
| `missing_mass_instance_count` | craft 预期 PART 中无唯一有效质量证据的 PART 数（缺失 + 唯一无效 + 重复，**不含** unexpected） |
| `invalid_instance_count` | **兼容别名** = `invalid_evidence_instance_count`；不再暗示"全部 unresolved 数量" |

## 数量守恒公式

```text
evidence_instance_count
  = valid_instance_count
  + invalid_evidence_instance_count
  + duplicate_evidence_instance_count
  + unexpected_evidence_instance_count

missing_mass_instance_count
  = missing_craft_instance_count          # 完全缺失
  + invalid_evidence_instance_count       # 唯一 evidence 无效（按 PART 计）
  + duplicate 涉及的 craft PART 数        # 重复而无法确定

complete_evidence
  = expected>0 且 输入一致 且 全部计数为 0 且 valid == expected
```

示例（craft 期望 index 0、1；evidence：index 0 两条 + index 1 一条有效）：
expected=2、evidence=3、valid=1、duplicate_evidence=2、
missing_mass=1、complete=false——**不会**因为 index 0 两条中有一条数值
正确就自动选择。示例（craft 仅 index 0 有效 + index 99）：valid=1、
unexpected=1、missing_mass=0、complete=false，index 99 进入 unresolved，
正式候选总质量与残差为 null。

## 合成缺失记录与 known_mass_subtotal

缺失 evidence 的 craft PART 生成可审计的合成记录（`synthetic=true`），
使 `unresolved_instance_evidence` 完整覆盖每个问题 PART，而不是只给出
"缺少有效质量"一句总提示。

`known_mass_subtotal_t` 只相加：属于 craft 预期 index、该 index 恰好一条
evidence、且通过匹配/状态/数值/part_source 白名单的实例。**不**加入
duplicate、unexpected、无效 evidence 与合成缺失记录。

## missing_mass 与 invalid_evidence 的区别

- `invalid_evidence_instance_count` 是**记录数**（有证据但证据本身坏）；
- `missing_mass_instance_count` 是 **craft PART 数**（没有可用质量证据的
  部件），包含完全缺失、唯一无效与重复三种情况；
- unexpected evidence 只是多余的记录，不增加 missing_mass。

## duplicate/unexpected 如何影响完整性

任何 missing / invalid / duplicate / unexpected / 输入不一致都会使
`complete_evidence=false` → `evidence_incomplete` → 正式候选总质量与残差
全部 null（只保留 known_mass_subtotal_t 作为部分和）。

## evidence_incomplete 诊断

汇总结构与实例原因（缺失数、无效数、重复数、多余数 + 各 invalid_reasons
计数），"原因 " 之后永不为空；缺少 evidence 时还会单独列出缺失的
craft index。

## 有数字不等于证据完整（6B.3B-R）

`complete_evidence` 只表示"全部 PART 均已**唯一匹配**并**完成质量解析**"，
不是"质量字段里碰巧有数字"。实例可用条件（白名单）：

| 条件 | 允许值 |
| --- | --- |
| `match_status` | `exact`、`normalized_unique` |
| `resolution_status` | `resolved_static`、`estimated_with_modifier` |
| `current_part_mass_estimate_t` | int/float JSON number（**bool 拒绝**）、有限、非负 |
| `part_source` | 唯一存在（parent_url + cache_order） |

反例：`resource_unresolved`/`invalid_mass`/`ambiguous` 等状态即使携带数字，
实例仍无效（`invalid_reasons` 给出 `invalid_match_status`、
`unresolved_mass_status`、`invalid_mass_value`、`missing_part_source` 等稳定
原因，而不是一句总提示）。

## 数量和 craft_index 守恒

```text
expected_instance_count：source.part_count（正整数）→ 否则 len(craft.parts)
```

- `expected == evidence` 实例数；craft_index 唯一且一一对应；
- 多记录、少记录、重复 index、多余 index、缺失 index 全部
  `evidence_incomplete`（对应稳定原因：`duplicate_craft_index` /
  `unexpected_evidence_instance` / `missing_craft_instance`）；
- 空实例列表永远不完整；
- `source.part_count` 与 `len(craft.parts)` 自身不一致 → 报告
  `input_structure_mismatch`，不得静默选一个值；
- 即使 `target_missing`，仍输出 `complete_evidence` 与
  expected/evidence/valid/invalid 计数——目标维度与证据维度是两个独立维度。

## target 状态与 evidence 状态是两个维度

优先级：`target_missing`/`target_invalid` → `evidence_incomplete` →
`matched_include_all` / `matched_excluding_non_flight` /
`ambiguous_scenario` / `unreconciled`。但 target 缺失/非法时证据计数照常
输出；证据不完整时不计算正式候选总质量与残差（全部 null），只保留
`known_mass_subtotal_t`（已知部分和，不把无效实例质量计入）。

布尔值边界：`totalMass=true` → `target_invalid`；
`current_part_mass_estimate_t=true` → 实例无效（`invalid_mass_value`）；
合法质量 0 仍可用。

## `.loadmeta.totalMass` 的定位

`totalMass` 是 KSP 在存档时写入的**整箭校验目标**（吨），不是级段质量表，
也不是逐部件质量之和的权威定义。它包含 KSP 对资源、动态模块（SSTU 集群、
ProceduralParts、燃料箱装载、LaunchClamp 等）的完整结算——本阶段逐部件
估计无法完全重现其语义，因此出现残差是预期现象，而不是应该被"修掉"的 bug。

严格解析：缺失 → `target_missing`；非数值/NaN/无穷/负数 → `target_invalid`
（保留 `source_total_mass_raw` 审计，绝不当作 0）；合法非负有限 → 允许对账。

## include_all 与 exclude_possible_non_flight 两个候选方案

| 方案 | 定义 |
| --- | --- |
| A. include_all | 所有**有效**（严格可用条件）实例的 `current_part_mass_estimate_t` 求和 |
| B. exclude_possible_non_flight | 再排除 `possible_non_flight_part=true` 的实例 |

possible_non_flight 统计与已知质量**分开**（6B.3B-R）：

- `possible_non_flight_instance_count`：所有被标记实例数（**含质量无效者**）；
- `known_possible_non_flight_mass_t`：其中质量有效部分的已知和；
- `possible_non_flight_mass_complete`：这些实例质量是否全部有效；
- `excluded_instance_evidence`：保留全部标记实例（含无效，带
  `invalid_reasons`）；
- 正式 `possible_non_flight_mass_t` 只在 `complete_evidence=true` 时输出。

被排除实例保留完整证据：craft_index、base_part、persistent_id、
match_status、resolution_status、current_part_mass_estimate_t、
contains_launch_clamp、possible_non_flight_part、part_source、
diagnostics、invalid_reasons。

## 残差符号

```text
residual = estimated_total - source_total_mass_t
```

- 正残差：逐部件估计高于 loadmeta（当前 20 个 craft 的 include_all 全是正）；
- 负残差：逐部件估计低于 loadmeta（排除非飞行候选后部分 craft 为负）。

同时输出相对残差 `residual / source_total_mass_t`（`source_total_mass=0`
时不除零）。

## 严格容差

只允许浮点与序列化误差容差：`absolute_tolerance_t = 1e-6`、
`relative_tolerance = 1e-7`，综合 `tolerance_t = max(1e-6,
abs(total)*1e-7)`。**不得**为了让真实 VAB 通过而放大到几十千克。

## 对账状态

`target_missing` → `target_invalid` → `evidence_incomplete` →
（两方案都匹配）`ambiguous_scenario` →（仅 A 匹配）`matched_include_all`
→（仅 B 匹配）`matched_excluding_non_flight` →（都不匹配）`unreconciled`。

`evidence_incomplete`：任一实例 `current_part_mass_estimate_t` 缺失/非法
时触发，`estimated_total_*` 字段置 null，只保留 `known_mass_subtotal_t`
（已知部分和，明确标为 partial）。

`ambiguous_scenario` 不自动选择；`nearest_scenario` 只是诊断提示
（`nearest_is_authoritative=false`），**没有"最接近即正确"规则**。

## 为什么不能用残差直接修正部件质量

- 残差是整箭级聚合，无法定位到具体部件；固定常数（如 0.0736 t）或按比例
  缩放都是无证据的猜测；
- 简单排除 LaunchClamp 也不能完成对账：无 LaunchClamp 的 craft 仍有正残差
  （Electron 系列 +0.0736 t），有 LaunchClamp 的 craft 排除后变负
  （最低 -0.326391 t）；
- 残差接近近似倍数（~0.0736、~0.1472、~0.2208）可能来自某类部件的质量
  语义，但当前只有相关性线索，不是质量公式。

## 当前 20 个真实 craft 的残差表

全部 `unreconciled`（include_all 与排除方案均超出严格容差）：

| craft | source_totalMass (t) | include_all (t) | residual include_all (t) | 非飞行数 | 排除后残差 (t) |
| --- | --- | --- | --- | --- | --- |
| Electron I 2112XL | 58.019329 | 58.092939 | +0.073610 | 0 | +0.073610 |
| Electron I 2114S | 59.953869 | 60.027474 | +0.073605 | 0 | +0.073605 |
| Electron I 2114XL | 87.942726 | 88.016335 | +0.073609 | 4 | -0.326391 |
| 1_2T Test | 53.268139 | 53.490346 | +0.222208 | 0 | +0.222208 |
| 1_5T Test | 59.444920 | 59.667132 | +0.222212 | 0 | +0.222212 |
| 1T_Test | 148.155334 | 148.376153 | +0.220819 | 3 | -0.079181 |
| 2_5T Test | 90.384705 | 90.533301 | +0.148596 | 4 | -0.251404 |
| 3T_Test | 152.933609 | 153.080824 | +0.147215 | 4 | -0.252785 |
| 5T_Test | 184.184967 | 184.332200 | +0.147233 | 3 | -0.152767 |
| Neutrons I 2430 | 107.894157 | 108.041385 | +0.147228 | 4 | -0.252772 |
| Neutrons I 2630 | 151.849838 | 151.997037 | +0.147199 | 4 | -0.252801 |
| Neutrons II 354S | 261.564148 | 261.784899 | +0.220751 | 4 | -0.179249 |
| Neutrons II 360 | 174.958435 | 175.179254 | +0.220819 | 4 | -0.179181 |
| Proton I 2210 | 48.337311 | 48.484538 | +0.147228 | 4 | -0.252772 |
| Proton I 2320 | 61.266586 | 61.413815 | +0.147229 | 4 | -0.252771 |
| Proton I 2322L | 115.778107 | 115.925335 | +0.147228 | 4 | -0.252772 |
| Proton I 2333XL | 179.143372 | 179.290604 | +0.147232 | 3 | -0.152768 |
| Proton II 3323S | 146.916229 | 147.137049 | +0.220820 | 3 | -0.079180 |
| Proton II 3324S | 188.758240 | 188.979088 | +0.220848 | 4 | -0.179152 |
| 自动保存副本 | 152.933609 | 153.080824 | +0.147215 | 4 | -0.252785 |

include_all 残差范围 +0.073605 ~ +0.222212 t；排除方案范围 -0.326391 ~
+0.222208 t。

### 残差聚类（只读相关性线索，非质量公式）

按 1e-5 取整后：+0.0736 t（3 个 Electron，共有 SSTU-SC-ENG-LR81-8048、
SSTU-SC-GEN-PDC）；+0.1472 t（8 个 craft，其中 6 个共有
probeCoreOcto.v2/batteryBankMini/KzResizableFairingBase 等，部分含
ProceduralPart）；+0.2208 t（3 个 craft，共有 KzResizableFairingBase）；
+0.2222 t（1_2T/1_5T，共有 SSTU-SC-ENG-LR81-8048、mk1pod.v2、
parachuteSingle，动态模块 ModulePartVariants）。

## 为什么目前不能确认 LaunchClamp 的总质量语义

三个 Electron craft 没有 LaunchClamp 却仍有 +0.0736 t 正残差；排除
LaunchClamp 后 1T/2_5T 等 craft 变为负残差。LaunchClamp 是否计入
`totalMass`、按什么质量计入，取决于 KSP 结算顺序，本阶段无法确认——
因此只报告两个候选方案，不自动排除。

## 下一阶段 6B.3C（未实现）

分析残差来自哪些 KSP/模组质量语义（模块结算顺序、ProceduralParts/
SSTU 动态质量公式、fairing 干质量、`amount=full` 的 TANK 容量语义、
LaunchClamp 质量等），基于对账取证字段（resolution 分布、动态模块分布、
PART 类型计数、资源/静态质量总和、最大质量实例、索引 fingerprint）定位。
