# 逐部件质量证据（阶段 6B.3A / 返工 6B.3A-R / 6B.3A-R2）

本阶段在 6B.2 确定性匹配的基础上，为每个 PART 实例提取**证据**：
基础质量、modMass 修正量、资源数量、资源密度与动态质量风险。

本阶段**不**生成最终级段质量、不计算发动机台数/Delta-V/运力、不修改
数据库正式火箭数据、不自动填写 `stage.wet_mass_t/dry_mass_t`。

## 返工修正

- 6B.3A-R："没有资源配置"与"资源状态缺失"严格区分；配置默认 RESOURCE 成为
  正式证据；显式非法 modMass 不再退回基础质量；同名重复/无名称资源不静默
  折叠；数值语义镜像；单一 `_row_is_resolved`；resolved/unresolved 守恒。
- 6B.3A-R2：PART 顶层 RESOURCE 与 ModuleFuelTanks/TANK **模板**严格分离，
  模板不再被误判为"craft 缺失资源"。

## KSP PART mass 的含义

KSP 部件 cfg 中的 `mass` 是该部件类型的**配置基础质量**（吨）。存档
（`.craft`）中的 `modMass` 是保存时刻的**模块修正量**（相对基础质量的差），
不是部件总质量。两者都不能直接当作实例最终质量：

```text
静态估计 = 基础质量(mass) + 修正量(modMass)   # 仅当两者均已知
```

## modMass 为什么是修正量而不是总质量

`modMass` 由模块在运行时改动（燃料箱装载、变体切换、可调尺寸等）写入存档。
一个 100 t 的液体火箭级，其油箱部件的 `modMass` 可能是 -90 t（把空箱质量
修正回基础值）——绝不能用它替代总质量。

6B.3A 起每个实例区分三种状态：

- `mod_mass_present`（字段是否存在）
- `mod_mass_raw`（原始文本）
- `mod_mass_t`（有限数值解析；缺失/NaN/无穷/非法时为 `null`）

缺失与合法 0 不再混淆；`modMass = 0` 是真实存在的零修正，缺失则是"没有
craft 修正证据"。

## 动态质量模块为何不能直接相信静态 mass

`ModuleFuelTanks`、`ProceduralPart`、`SSTUModularEngineCluster`、
`SSTUModularFuelTank`、`ModulePartVariants`、`TweakScale`、
`B9PartSwitch`、`InterstellarFuelSwitch` 会动态改变实例质量：

- 有合法 modMass：`static_dry_mass_estimate_t = base + mod`，状态
  `estimated_with_modifier`，置信度**不高**（medium）；
- modMass 缺失/非法：状态 `dynamic_unresolved`，静态估计为 `null`；
- 本阶段**不**推导 ProceduralParts / SSTU / TweakScale 的动态质量公式。

## RESOURCE 与 ModuleFuelTanks/TANK 的镜像关系

真实 VAB 存档中，顶层 `RESOURCE` 与 `ModuleFuelTanks/TANK` 记录的是**同一份
资源状态**：

- 顶层 craft `RESOURCE` 是数量首选证据；
- 镜像按**数值语义**比较（`"10"` 与 `"10.0"` 相同）；非法数值永不视为
  镜像；amount 与 maxAmount 均等价才标记 `mirrored`（只计算一次）；
- 同名但数值不一致 → `resource_tank_mismatch` 诊断，以 craft RESOURCE 为准；
- craft RESOURCE 缺失、TANK 存在 → `tank_fallback` 回退（降低置信度并诊断）；
- **同名重复**的 craft RESOURCE / TANK 全部保留（不覆盖），标记
  `duplicate_craft_resource` / `duplicate_tank_resource`，这些行视为
  `unresolved`（实际状态无法确定）；
- 无名称的 RESOURCE / TANK 不静默丢弃，产生 `unnamed_resource` /
  `unnamed_tank` 诊断并标为 unresolved；
- PART 配置中的 RESOURCE / ModuleFuelTanks 只是默认容量证据，**不覆盖**
  craft 实际 amount。

回归测试保证：同一份燃料同时出现在 RESOURCE 与 TANK 时**不会**双倍计算。

## "没有资源配置"与"资源状态缺失"是两种不同情况

| 情况 | current_resource_mass_t | 状态 |
| --- | --- | --- |
| 配置与 craft 都没有任何资源 | `0.0`（精确零） | resolved |
| PART 顶层 RESOURCE 声明了资源，craft 未保存状态 | `null` + `resource_state_missing` 诊断 | resource_unresolved |
| craft 保存了状态 | 按 craft amount 计算 | 正常解析 |

配置默认 amount **不是** craft 当前 amount：`config_default_only` 行只保留
`config_default_amount_raw` / `config_default_max_amount_raw` /
`configured_maximum_mass_t` 证据，`amount_raw` 与 `current_mass_t` 为 null。

## PART 顶层 RESOURCE 与 ModuleFuelTanks/TANK 模板（6B.3A-R2）

安装配置中的资源证据分两类，**不得混淆**：

**A. PART 顶层 RESOURCE（`record["resources"]`）**——部件预制配置中的默认
实际资源：

- craft 缺少对应状态时生成 `config_default_only` 行 + `resource_state_missing`
  诊断（实例 resource_unresolved）；
- `config_default_present` / `config_default_amount_raw` /
  `config_default_max_amount_raw` 只由这一类决定；
- `configured_maximum_mass_t` 只能由合法顶层 RESOURCE 默认容量 × 唯一密度
  计算（模板 maxAmount 永远不参与）。

**B. ModuleFuelTanks/TANK（`record["fuel_tanks"][...]["tanks"]`）**——动态
油箱模块的燃料模板/候选燃料/换算参数：

- **不证明资源已实际安装**；
- craft 没有该资源时：不生成任何状态行、不产生 `resource_state_missing`、
  不影响 resolved/unresolved；
- `amount="full"` 等模板标记原样保留（不是非法数值、不是 craft 数量）；
- 模板 maxAmount 不能乘以密度得到实例最大资源质量；
- 只作为审计证据保留：实例级 `module_fuel_tank_options` 与行级同名
  `module_fuel_tank_options`。

真实案例：`mk1pod_v2` 的 ModuleFuelTanks 模板声明 `Hydrazine`（amount=full，
maxAmount=50），但三个 craft（1_2T / 1_5T / 2_5T Test.craft）只保存了
ElectricCharge 状态。**缺少 Hydrazine craft 节点不代表状态缺失**——模板只
说明该模块允许 Hydrazine；旧实现（6B.3A-R）因此错误标记
`resource_state_missing` 并计算 `configured_maximum_mass_t=0.0502`，
R2 已修正。

craft 已安装资源与模板同名时（如 ElectricCharge）：craft RESOURCE/TANK
是当前数量与保存容量的唯一依据；模板附加为 option 证据；模板
`maxAmount=50` 不覆盖 craft `maxAmount=1000`，也不产生 mismatch
（`tank_mismatch` 只用于比较 craft 顶层 RESOURCE 与 craft 保存的
ModuleFuelTanks/TANK，不用于比较配置模板）。

## 容量字段区别

- `saved_maximum_resource_mass_t`：**craft 保存**的 maxAmount 容量；
- `configured_maximum_resource_mass_t`：**配置默认**容量（唯一合法时）；
- `maximum_resource_mass_t`：兼容别名，恒等于 `saved_maximum_resource_mass_t`
  （craft 保存容量），**绝不**混入配置默认容量。

## 显式非法 modMass 为什么不能退回基础质量

`mod_mass_present = true` 但 `mod_mass_t = null`（存在但无法解析/NaN/无穷）
是**显式非法修正量**：修正量丢失即实例保存状态不完整，不能假装"已解析"：

- 无动态质量模块 → `invalid_mass`，`static_dry_mass_estimate_t = null`；
- 有动态质量模块 → `dynamic_unresolved`；
- 保留 `invalid_mod_mass` 诊断；不得把非法值转换为 0。

只有 `modMass` 字段**完全不存在**时，静态部件才可保留基础质量估计
（confidence = medium）。合法 `modMass = 0` 与缺失严格区分，可正常计算。

## 资源密度的单位和来源

`density_t_per_unit` 来自**唯一**的 `RESOURCE_DEFINITION`（吨/单位）：

```text
资源质量(t) = amount × density_t_per_unit
```

- 多个同名定义 → `ambiguous_density`，即使 primary 明确也不静默选取；
- 无定义 → `missing_density`；非法/负密度 → `invalid_density`；
- `density = 0` 是合法无质量资源（如 ElectricCharge），不算缺失；
- 负 amount、负 maxAmount、amount > maxAmount 一律拒绝（对应诊断）。

## 各 resolution_status 的含义

| status | 含义 |
| --- | --- |
| `resolved_static` | 基础质量合法、无动态质量模块（或 modMass 合法）；静态估计可用 |
| `estimated_with_modifier` | 有动态质量模块且 modMass 合法；仅保存状态估计，非确认干质量 |
| `dynamic_unresolved` | 有动态质量模块但 modMass 缺失/非法，无法估计 |
| `resource_unresolved` | 存在未解析的资源行（密度缺失/歧义/非法或数量非法） |
| `match_unavailable` | 实例未唯一匹配（missing/ambiguous/index_unavailable），不计算 |
| `invalid_mass` | 基础质量缺失/非法/负，或静态估计为负；绝不用 0 回退 |

优先级：`match_unavailable > invalid_mass > dynamic_unresolved >
resource_unresolved > estimated_with_modifier > resolved_static`。

合计字段规则：只要任何参与项未解析，对应合计为 `null`（不把缺失当 0）：
`current_part_mass_estimate_t = static_dry_mass_estimate_t +
current_resource_mass_t`（两者均非 null 时）。

## 本阶段为何不自动填充级段质量

- 输出仍是"证据/估计"：`estimated_with_modifier` 与 `dynamic_unresolved`
  实例不能产生可信级段质量；
- LaunchClamp 等非飞行部件已标记（`contains_launch_clamp` /
  `possible_non_flight_part`，依据索引记录中的 `LaunchClamp` 模块），但
  是否排除出整箭质量留给 6B.3B；
- 预览仍为 `needs_review`，`requires_mass_confirmation = true`，
  `stage.wet_mass_t/dry_mass_t` 保持 0 占位，个别质量证据缺失只生成结构化
  警告，不允许直接导入正式数据库。

## 下一阶段 6B.3B（未实现）

整箭质量汇总、`.loadmeta.totalMass` 对账、LaunchClamp 排除决策与残差
诊断。届时只使用 `match_status ∈ {exact, normalized_unique}` 且
`selected` 唯一的实例。

## resolved_instances 与 unresolved_instances

- `resolved_instances` = `resolved_static` + `estimated_with_modifier`；
- `unresolved_instances` = 其余全部（`dynamic_unresolved` +
  `resource_unresolved` + `match_unavailable` + `invalid_mass`）；
- 两者之和恒等于 `total_instances`；
- `evidence_instances` 仅表示"产生了证据记录"（存在唯一匹配），**不**代表
  已解析，调用方应优先使用 resolved/unresolved 计数。

## 真实 VAB 统计（本机 20 craft / 389 实例，6B.3A-R2 后）

- resolved_instances 389 / unresolved_instances 0；
- status：`resolved_static` 185、`estimated_with_modifier` 204，其余 0；
- 三个 `mk1pod.v2` 实例（1_2T / 1_5T / 2_5T Test.craft）均为
  `estimated_with_modifier`，resource_evidence 只有 ElectricCharge，
  Hydrazine 仅作为 `module_fuel_tank_options` 审计证据；
- 动态模块出现次数：ModuleFuelTanks 95、SSTUModularEngineCluster 67、
  ModulePartVariants 59、ProceduralPart 4；
- 资源行 184 全部解析；镜像行 162；missing/ambiguous/invalid density 0；
  config_default_only / resource_state_missing / duplicate / mismatch 0；
- possible_non_flight：60 个 `launchClamp1` 实例。

## 下一阶段 6B.3B（未实现）

整箭质量汇总、`.loadmeta.totalMass` 对账、LaunchClamp 排除决策与残差
诊断。届时只使用 `match_status ∈ {exact, normalized_unique}` 且
`selected` 唯一的实例。
