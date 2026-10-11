# KSP 受控质量实验（阶段 6B.3C.3A-R3）

## 为什么现有 20 个样本无法继续归因

现有 VAB 样本中 `parachuteSingle` 与 `mk1pod.v2` 的计数**完全共线**
（两个部件总是成对出现，20 个样本无一例外）。共线特征无法用回归或
匹配差分分离：

- MFT 已由静态 IL 确定为 `massIsAdditive = true`、modifier = 0（pod 质量
  无净修正），不能解释 +0.075 t 残差；
- RealChute 的 massDelta 链已由修复后的 IL 再确认（caseMass + ΣChuteMass −
  partPrefab.mass = +0.01901424 t），但应用于基线后残差变为 +0.094 t，
  与实测 +0.075 t 不符；
- 四假设全部 contradicted；剩余归因只能来自**新的、打破共线的数据**。

本实验用四份最小 craft 逐一改变降落伞数量与直径，用**配对差分**直接测量
单个降落伞对 `loadmeta.totalMass` 的实际贡献，从而把
`parachuteSingle` 的质量行为与 `mk1pod.v2` 彻底分开。

## 工具与当前状态

```text
ksp_mass_control_experiment.py prepare
    [--control-core PART] [--ksp-root PATH] [--output DIR]
        → 只读检查安装/指纹、确定性安全控制核心选择并绑定到 manifest、
          生成中文操作清单、创建空数据接收目录
        → 最终状态：awaiting_ksp_capture
ksp_mass_control_experiment.py preflight
    --ksp-root PATH --vab-dir <Ships/VAB> [--json]
        → 只读真实环境预检（指纹、控制核心与安装兼容性、craft/loadmeta
          配对统计与拒绝原因、totalMass 观测范围）
ksp_mass_control_experiment.py analyze
    --input <实验目录> [--json]         → 严格校验四组 craft/loadmeta
                                          （配对、控制核心、对称、变量隔离、
                                          指纹、输入 SHA-256 证据清单），
                                          配对差分与三个质量模型
```

退出码：`analyzed` / `awaiting_ksp_capture` / `preflight_complete` → 0；
`invalid_design` / `invalid_setup` / `invalid_experiment_setup` → 2。

**当前状态：`awaiting_ksp_capture`。** 本阶段不运行 KSP、不写入 KSP 目录、
不伪造任何 `.craft`/`.loadmeta`；所有结论必须来自用户在 KSP 中生成并复制
到 `experiments/control_mass/input/` 的真实数据。**尚未产生任何真实实验
结论。**

## 绑定的安全控制核心（manifest 唯一绑定，物理可用性优先）

`prepare` 从最终 `ModuleManager.ConfigCache` 索引中确定性筛选安全控制核心：

1. 来自最终 ConfigCache 索引；
2. 包含 `ModuleCommand`；
3. 有限且有效的**固定基础质量**（> 0）；
4. 不含任何 `ModuleEngines` / `ModuleEnginesFX`（及任何 `ModuleEngines*`
   变体）；
5. 不含当前 `DYNAMIC_MASS_MODULES` 中的任何模块；
6. 不含 RealChute / ModuleFuelTanks / ProceduralPart / SSTU / PDC /
   TweakScale / B9PartSwitch / ModulePartVariants 等质量可变模块；
7. **`attachRules` 必须合法**（缺失/字段数错误/非法布尔 → 排除），且
   `allowSrfAttach=true`（可径向承载子部件）、`allowStack=true` 并拥有至少
   一个非空 `node_stack_*` 节点（`parachuteSingle` 通过底部节点或径向
   安装，两条路径都必须可能）。

排序以**实验稳定性优先，再考虑质量**：正常 `Pods` 类无人探测核心（有顶部
stack 节点）排在最前；“最轻”不再压倒物理可用性。**AR202 被排除**：
`mumech_MJ2_AR202` 的 attachRules=`0,1,0,0,0`（`allowSrfAttach=0`、
`allowStack=0`、无任何 stack 节点），只是只能径向挂到别人身上的附件外壳，
不能承载实验降落伞。

**当前选中的控制核心：`probeCoreOcto2_v2`**（探测动力-OKTO2，Pods 类，
补丁后质量 0.04 t；attachRules=`1,0,1,1,0`，allowSrfAttach=true，
allowStack=true，stack 节点 `node_stack_top`/`node_stack_bottom`；
预检确认 parachuteSingle 安装兼容（stack 路径可用））。选择证据全部来自
补丁后配置（`safety_checks` 逐项列出，`selection_reason` 记录理由），
manifest 保存其 `name/title/mass_t/parent_url/category/attach_rules/
bulkhead_profiles/stack_nodes/crew_capacity/modules/resources/safety_checks/
selection_reason/sha256`；sha256 为**包含安装规则**的规范化摘要。

`prepare --control-core <part_name>` 可显式指定；不满足任一安全条件 →
`invalid_setup` 失败封闭，绝不静默回退或硬编码部件名。`analyze` 用**当前
索引**重新验证：仍存在、仍满足全部安全条件、规范化摘要哈希仍一致。

### 组合安装计划（父子双方共同判定）

安装可行性**不能只看控制核心自身**的 `allowSrfAttach`/`allowStack`，必须
由父子双方共同判定（`_experiment_attachment_plan`）：

```text
surface_path =
    chute.attach_rules.srfAttach        ← 子部件必须能径向附着
    AND core.attach_rules.allowSrfAttach
stack_path   =
    chute.attach_rules.stack
    AND core.attach_rules.allowStack
    AND 存在兼容且可用的 stack nodes（按 ConfigCache 中的节点顺序）
```

真实补丁后 `parachuteSingle` 为 `attachRules = 1,0,0,1,0`
（`srfAttach=false`），**不能作为子部件径向附着**；当前组合实际依赖
stack 路径。M2 需要两个独立降落伞：stack 路径下控制核心必须提供两个
可独立使用的节点——当前 `probeCoreOcto2_v2` 使用
`node_stack_top` + `node_stack_bottom`；M1 与 M3 使用同一指定节点
（`node_stack_top`），M2 分别使用 top 与 bottom，**禁止对称模式、禁止把
一个降落伞挂到另一个降落伞上（串联）**。manifest 保存结构化
`attachment_plan`；`analyze` 校验实际 craft 连接符合该计划（把连接解析为
“哪个基础部件通过哪个节点连接到哪个基础部件”，不比较实例 ID 本身）。

### 迁移（schema v2 → v3，append-only history）

当前空 `input/` 下的旧 manifest（schema v2，绑定了错误的 AR202）已自动
迁移并补齐审计记录：保留原 `created_at`，`migration_history` 为
append-only 列表：

```json
[{"from_schema": 2, "to_schema": 3,
  "old_control_core": "mumech_MJ2_AR202",
  "new_control_core": "probeCoreOcto2_v2",
  "reason": "old core cannot host experiment chute"}]
```

幂等 `prepare` 保留该记录（不重复追加）；`input/` 非空时**禁止**任何自动
补写或迁移（`input_not_empty` 拒绝）。已损坏（无法解析）的 manifest 同样
拒绝覆盖（`manifest_corrupt`）。

## 四份 craft 的人工操作步骤

四份 craft 必须在**同一个独立 KSP 存档**中手动搭建（新建存档或独立复制的
KSP 安装）：

| craft | 内容 | 降落伞数 |
| --- | --- | --- |
| `M0_control` | 只有一个最小控制核心（= 绑定的核心） | 0 |
| `M1_one_chute_default` | M0 + 一个默认 `parachuteSingle` | 1 |
| `M2_two_chutes_default` | M0 + 两个独立默认 `parachuteSingle` | 2 |
| `M3_one_chute_changed_diameter` | 从 M1 复制，只改展开直径 | 1 |

### M0_control

1. 在 VAB 中把**绑定的控制核心**（`probeCoreOcto2_v2`）作为根部件放下；
2. 不添加其他部件；推进剂与可变资源为空（或四份 craft 完全一致）；
3. 保存为 `M0_control`。

### M1_one_chute_default

1. 加载 M0，把 `parachuteSingle` 安装到核心的 **`node_stack_top`** 节点
   （当前组合为 stack 路径；径向路径不可用）；
2. **关闭对称**，使用默认 RealChute 参数；
3. 保存 → 退出 VAB → 重新加载该 craft → **再保存一次**；
4. 不修改其他部件和资源。

### M2_two_chutes_default

1. 加载 M0，添加两个 `parachuteSingle`；
2. **不使用对称复制**：逐个放置两个独立实例，参数一致（均默认）；
3. 一个安装到 `node_stack_top`，另一个安装到 `node_stack_bottom`
   （两个独立节点，各挂一个，**禁止把第二个挂到第一个降落伞上**）；
4. 保存为 `M2_two_chutes_default`。

### M3_one_chute_changed_diameter

1. 加载 M1 后立即另存为 `M3_one_chute_changed_diameter`；
2. 在 RealChute UI 中把展开直径改为与 M1 明显不同的值（建议 15–26 m，
   避开默认 20.7 ± 0.5 m）；
3. 不得修改 caseMass、材料、根部件、资源或其他参数；
4. 保存后打开 craft 文件，确认 ProceduralChute 的 `depDiam` 已更新。

## 找到 KSP 生成的 craft/loadmeta

KSP 把 craft 与 loadmeta 保存在存档目录：

```text
<KSP>/saves/<存档名>/Ships/VAB/
```

每个 craft 对应一个 `.craft` 与同名 `.loadmeta`（保存/飞行时由 KSP 生成）。
**loadmeta 是 KSP 生成的文件，不要手工编辑。**

## 复制到实验输入目录

把下列 8 个文件复制到仓库内的数据接收目录：

```text
experiments/control_mass/input/
    M0_control.craft              M0_control.loadmeta
    M1_one_chute_default.craft    M1_one_chute_default.loadmeta
    M2_two_chutes_default.craft   M2_two_chutes_default.loadmeta
    M3_one_chute_changed_diameter.craft
    M3_one_chute_changed_diameter.loadmeta
```

只要 `input/` 中还有任何文件，`prepare` 就**拒绝覆盖或迁移**
manifest/instructions。相同指纹 + 相同控制核心 + 空输入时重复 `prepare`
幂等；不同指纹/不同核心 → 拒绝；损坏 manifest → 拒绝；旧 schema 仅在空
输入下自动迁移。

## 运行 preflight（只读环境预检）

```powershell
C:\ProgramData\anaconda3\python.exe ksp_mass_control_experiment.py preflight `
  --ksp-root "D:\SteamLibrary\steamapps\common\Kerbal Space Program" `
  --vab-dir "<存档>/Ships/VAB" --json
```

预检报告：ConfigCache/DLL 指纹；最终选中的控制核心及 `parachuteSingle`
安装兼容性（attachRules 兼容矩阵：径向路径 / stack 路径）；扫描到的
`.craft/.loadmeta` 配对数；`shipName / partCount / partNames / type /
version` 的通过/拒绝计数；`totalMass` 有效范围与**观测到的小数位数范围**
（**仅诊断，不用于推导精度**）；被拒绝样本的机器可读原因。错配的自动存档
（如 `自动存档飞船.craft` 的 loadmeta 来自其他飞船）会被正确列为拒绝样本，
不会导致预检崩溃。

## 运行 analyze

```powershell
C:\ProgramData\anaconda3\python.exe ksp_mass_control_experiment.py analyze --input experiments/control_mass --json
```

- 8 个文件未齐 → `experiment_status = awaiting_ksp_capture`（列出缺失文件）；
- 校验失败 → `experiment_status = invalid_design` 并列出**机器可读 code 的
  具体差异**（根部件 ≠ 绑定控制核心 / 控制核心配置哈希变化 / 部件集合差异
  / 资源差异 / 降落伞数量错误 / M1/M2 参数不一致 / M3 除直径外还有其他
  变化 / 对称连接 / 控制核心 mod_mass 或状态不一致 / 被禁止的质量
  modifier / craft/loadmeta 配对或解析失败 / version 缺失或不一致 /
  指纹与 prepare 不一致），此时**不给出任何支持/反驳判定**；
- 全部通过 → `experiment_status = analyzed`。

### .craft / .loadmeta 配对校验（每对 M0..M3）

- craft 内部 `ship` 名 == 场景名；`loadmeta.shipName` == 对应 craft 的
  ship 名（**仅改名的旧 loadmeta** 被拒）；
- `loadmeta.partCount` == craft 实际部件数；`partNames`（去重集合）==
  craft 部件组成；`type` == `VAB`；`totalMass` 有限正数；
- 四组文件的 KSP `version` 一致；**craft 或 loadmeta 缺失 version 必须
  报告缺失**（`craft_version_missing` / `loadmeta_version_missing`），
  不能因“所有缺失值都没进集合”而误判一致。

### 变量隔离校验

- M0 严格只有一个部件（= 控制核心）；M1/M2/M3 = 控制核心 + 1/2/1 个伞；
- 实验降落伞**不得存在 `sym` 对称连接**；
- 四份 craft 控制核心的质量相关实例状态一致（base part / modules /
  resources / `mod_mass_present` / `mod_mass_raw` / `mod_mass_t` / 其他
  已解析的质量 modifier 状态）；实例 ID、位置、旋转与普通连接忽略；
- 质量 modifier 检查 = 静态 FORBIDDEN_MODULES ∪ **当前索引的
  DYNAMIC_MASS_MODULES**；
- M1/M2 的降落伞质量相关状态完全相同；M3 相对 M1 只能修改展开直径。

### 输入证据清单

分析结果记录全部 8 个输入文件的 SHA-256，形成可复核的证据清单。

## 数值精度模型（动态 binary32 归一化，schema v3）

已只读检查全部存档中的 59 个 VAB `totalMass`：小数位数在 **5–9 位之间**
分布（如 `152.82431`、`177.892502`、`49.0145302`、`1.86859989`、
`0.570662856`）；59/59 都能在量化为 binary32 后用 `.9g` 往返回原文本，
0/59 的十进制解析值与精确 binary32 值完全相等（最大差约 4.7e-7 t）。
**固定七位小数假设不成立**，也不通过统计小数位数猜测精度。新模型：

- **序列化模型**：`ieee754_binary32_round_trip`；
- 对每个 totalMass 分别记录三种数值：
  - `parsed_decimal_t` —— 十进制字符串解析值；
  - `stored_f32_t` —— 归一化后的 binary32 值（`float32(parsed)`）；
    **所有差分（M1−M0 / M2−2·M1+M0 / M3−M1）都使用 stored_f32_t，
    不再使用十进制解析近似值**；
  - `decimal_roundtrip_delta_t` —— `abs(parsed − stored)`，十进制往返
    显示误差，**单独报告，绝不并入量化误差**；
- 量化误差使用**舍入区间中点**：对正有限 binary32 `stored` 及其相邻值
  `lower_neighbour` / `upper_neighbour`（float32 位模式 bits±1）：

  ```text
  lower_midpoint = (lower + stored) / 2
  upper_midpoint = (stored + upper) / 2
  quantization_bound_t = max(stored − lower_midpoint,
                             upper_midpoint − stored)
  ```

  即相邻间距的一半量级（2 的幂附近上下 ULP 不对称时取较大半区；
  正常数/次正规数边界与 `+0` 正确处理；非有限值、负值与超出 binary32
  范围的值失败封闭；本实验 totalMass 必须为正）；
- 误差传播（按系数绝对值，使用 `quantization_bound_t`）：
  - `M1−M0`：`b1 + b0`
  - `M2−2·M1+M0`：`b2 + 2·b1 + b0`
  - `M3−M1`：`b3 + b1`
- **预测端误差单独记录**：`|预测值| × predicted_float32_error_factor`
  （2^-21，覆盖 float32 结算链 ≤8 次运算的 2^-24 相对误差与 float64
  往返）；
- 最终判定容差 = 动态量化误差传播 + 预测端误差 + 明确的实验裕量
  `experiment_margin_t`；
- 报告中每组质量输出：`raw_decimal`、`parsed_decimal_t`、
  `stored_f32_t`、`decimal_roundtrip_delta_t`、`serialization_model`、
  `lower_neighbour_t`、`upper_neighbour_t`、`lower_midpoint_t`、
  `upper_midpoint_t`、`quantization_bound_t`；每条差分/线性/直径检查输出
  `observed_error_bound_t`、`predicted_error_t`、`verdict_tolerance_t`；
- 固定的 `loadmeta_resolution_t / single_value_max_rounding_error_t /
  model_tolerance_t ...` 等常量**已删除**（schema v3 中未知字段失败
  封闭，绝不参与判定）；不允许把固定容差调大来掩盖问题；
- manifest 中未知、缺失或非法的容差字段 → `invalid_experiment_setup`；
  `diameter_min_delta_m` 单位是米。

## 差分定义与三种模型

```text
observed_one_chute_t      = stored_f32(M1) − stored_f32(M0)
observed_two_chutes_t     = stored_f32(M2) − stored_f32(M0)
observed_diameter_effect_t = stored_f32(M3) − stored_f32(M1)
```

（所有观测增量基于归一化 binary32 值，不是十进制解析近似值。）

从补丁后配置与 craft 证据计算：

```text
prefab_part_mass_t            parachuteSingle 补丁后 PART mass
case_mass_t                   craft 保存的 caseMass
chute_mass_t                  float32(π·d²/4) × areaDensity
realchute_mass_delta_t        caseMass + ChuteMass − prefab
predicted_settled_part_mass_t prefab + realchute_mass_delta
                              = caseMass + ChuteMass
```

| 模型 | 单个降落伞预测增量 | 含义 |
| --- | --- | --- |
| `prefab_base_only` | 补丁后 PART mass | 降落伞按配置质量计入 loadmeta |
| `realchute_settled_mass` | caseMass + ChuteMass | RealChute 结算质量真正进入 loadmeta |
| `part_effectively_excluded` | ≈ 0 | 降落伞对总质量无贡献 |

每个模型输出 `applicable / evidence_complete / predicted_increment_t /
observed_increment_t / absolute_error_t / observed_error_bound_t /
predicted_error_t / verdict_tolerance_t / verdict`，判定只能是
`supported` / `contradicted` / `insufficient_evidence`。不选择“最接近”的
模型冒充已证实结论。

## 为什么用配对差分而不是绝对质量

控制核心本身可能带有无法完全建模的质量状态（内部资源、模块结算差异），
绝对总质量不可靠。配对差分把核心质量相消：

- **M1 − M0**：隔离一个降落伞的实际增量；
- **M2**：检查线性与实例计数（两个独立实例是否两倍增量）；
- **M3**：检查 RealChute 的 chute mass 是否真正进入 loadmeta；
- 该实验用于打破现有样本中 `parachuteSingle` 与 `mk1pod.v2` 的完全共线。

绝对质量仅用于诊断，不作为模型判定依据。

## 失败或污染时如何重新采集

- `part_set_mismatch` / `chute_count_mismatch` / `resource_mismatch` /
  `symmetry_forbidden` / `core_state_inconsistent` → 删除 input/ 中对应
  文件，重新搭建并重新保存；
- `m3_extra_changes` / `m3_diameter_not_changed` → 从 M1 重新复制，只改
  depDiam；
- `loadmeta_*_mismatch` / `craft_ship_name_mismatch` / `*_version_missing`
  → 文件错配、改名或缺失字段：重新复制正确的同名文件对；
- `fingerprint_mismatch` / `control_core_config_changed` /
  `control_core_no_longer_safe` → 模组、缓存或核心部件变了：清空 input/
  后重新运行 `prepare`；
- 完成后重新运行 analyze。

## 数据真实性声明

`tests/` 中的实验夹具全部为 **synthetic**（文件内容带
`// SYNTHETIC FIXTURE - not real KSP data` 标记，仅用于测试工具逻辑），
其分析结果不构成真实 KSP 证据。正式结论只能来自用户采集的真实
craft/loadmeta 并通过 analyze 校验。**当前仍在等待用户采集
（awaiting_ksp_capture），尚未得到任何真实 KSP 结论。**
