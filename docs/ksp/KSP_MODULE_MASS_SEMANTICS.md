# RealChute / ModuleFuelTanks 质量修改语义只读取证（阶段 6B.3C.2B-R2）

本阶段只分析已安装模组的配置、程序集元数据、方法 IL 与三份真实 craft，
**不修改生产质量公式，不创建或修改 KSP 存档**。

## 程序集与指纹（实际读取）

| 程序集 | 路径 | SHA256 | 版本（元数据） |
| --- | --- | --- | --- |
| RealChute.dll | `GameData/RealChute/Plugins/RealChute.dll` | `884ACB3F...B56D5`（匹配任务指纹） | 1.4.9059.6703（version 文件 1.4.9.5） |
| RealFuels.dll | `GameData/RealFuels/Plugins/RealFuels.dll` | `6C6629ED...77B5B`（匹配任务指纹） | 1.0.0.0（version 文件 15.14.0） |

读取方式：自研最小 ECMA-335 元数据/IL 只读解析器（`server/ksp_dotnet_il.py`，
metadata-only，不反射加载、不实例化、不调用、不写盘）。哈希不匹配时
`assembly_mismatch`，**拒绝套用已知语义**。

## 6B.3C.2B-R2 解析器修复（本阶段）

### 操作码表按独立权威全表校正

- 本机 `System.Reflection.Emit.OpCodes`（PowerShell Add-Type 只读转储）作为
  独立权威，转储结果以**固定清单**提交到 `tests/fixtures/opcodes_dotnet.json`
  （199 个有效单字节 + 27 个有效 0xFE 指令；测试夹具不从此模块的字典生成，
  避免自证正确）；Windows 上另有**实时**一致性测试（每次测试重新转储
  OpCodes 并与生产表比对）。
- 修正的系统性错位（R2 前版本）：`0x79 unbox`（原 conv.u2）、`0x7A throw`、
  `0x82–0x89 conv.ovf.*.un`（原 conv.ovf.*）、`0x8B conv.ovf.u.un`、
  `0x8C box`、`0x8D newarr`、`0x8E ldlen`、`0x8F ldelema`、
  `0x90–0x9A ldelem.*`、`0x9B–0xA2 stelem.*`、`0xA3 ldelem`、`0xA4 stelem`、
  `0xA5 unbox.any`、`0xB3–0xBA conv.ovf.{i,u}{1,2,4,8}`（原错位 + 缺 0xBA）、
  `0xC2 refanyval`/`0xC3 ckfinite`/`0xC6 mkrefany`、`0xD0 ldtoken`、
  `0xD1–0xE0 conv.u2…conv.u`；**leave/leave.s 在 0xDD/0xDE**（原误写
  0xB6/0xB7）。
- 原被错误占用的保留值（`0x77`、`0x78`、`0xA6–0xB2`、`0xBB–0xC1` 等）已释放；
  `0xF8–0xFF`（prefix7…prefixref）与 `0xFE 0x12 unaligned.` / `0xFE 0x13
  volatile.` / `0xFE 0x14 tail.` / `0xFE 0x16 constrained.` / `0xFE 0x1E
  readonly.` 归类为 **prefix**（Meta 流控，不是可执行指令）；表中未出现的
  值一律是 reserved，解码即报错停止。
- operand 类型与长度随名称一起校正（u1/u2/i1/i4/i8/r4/r8/tok4/br1/br4/
  switch），验收计数：单字节与 FE 的名称/operand 不匹配均为 0，保留值被当作
  普通指令 = 0。

### 解码失败封闭（decode_complete / decode_errors / first_error_offset）

- 保留操作码、截断 operand（含 switch 表）、`0xFE` 前缀截断都会**停止解码**，
  剩余字节不再被解释成指令；分支/switch 目标必须落在方法体内且位于指令边界，
  否则 `invalid_branch_target` 并使 `decode_complete=false`。
- `method_evidence()` 同时携带方法体读取状态（`il_error`/`il_body_status`）
  与 IL 解码状态（`decode_complete`/`decode_errors`/`first_error_offset`）。
- `methods_writing_field()` / `methods_calling_method()` 统计
  `method_bodies_unparsed` 与 `method_bodies_decode_incomplete`，并给出
  `scan_complete`；**只要存在无法检查的方法体，“零写入者”就只能判定为
  insufficient**，不能推导 CLR 零初始化后的最终运行状态。
- 真实 DLL 验证：RealFuels 1173 个 / RealChute 674 个方法体全部
  `decode_complete=true`（旧表会把 `TankDefinition.Load` 等方法解码出
  伪指令）。

### token 与字段签名边界

- token 统一拆分为 `table_id`/`row_id`：整数 `0x04000002`、字符串
  `'0x04000002'`、零填充 8 位无前缀十六进制字符串 `'04000002'`、十进制
  字符串 `'67108866'` 四种写法结果完全一致；无前缀字符串规则（文档化）：
  `0x` 前缀 → 十六进制；含 `a–f` → 十六进制；恰好 8 位全十六进制数字且
  首位为 `0` → 十六进制（规范零填充写法）；否则按十进制。歧义规则：8 位
  全数字且**不以 `0` 开头**的字符串一律按十进制读（如 `'67108866'` =
  0x04000002；`'20000002'` = 十进制 20000002，而不是 0x20000002 —— 想写
  十六进制请加 `0x` 或零填充到 8 位）。错误表类型被拒绝
  （`invalid_token_table`）；`field_token`/`method_token` 输出不再重复
  拼接 table id。
- Field 签名 blob 不存在或越界时返回 `signature_error=blob_unresolvable`，
  绝不计算空字节 SHA-256 冒充有效签名。

## RealChute 质量链（IL 证据，修复后重新确认）

```csharp
// RealChuteModule.UpdateMass()
if (partPrefab == null) { massDelta = 0; return; }
massDelta = caseMass + Sum(p => p.ChuteMass) - partPrefab.mass;
// GetModuleMass(defaultMass, sit) { return massDelta; }   // IL: ldfld massDelta; ret
// IPartMassModifier.GetModuleMassChangeWhen() { return CONSTANTLY; }
//                                                    // IL: ldc.i4.0; ret
// Parachute.get_ChuteMass()   = get_DeployedArea() * mat.get_AreaDensity()
// RCUtils.GetArea(d)          = conv.r4(Math.PI * d * d / 4)
// MaterialDefinition.get_AreaDensity() = ldfld areaDensity
```

parachuteSingle 实例（三份特殊载具各 1 个）：caseMass = 0.075000003 t；
deployedDiameter = 20.7（craft ProceduralChute CHUTE 行）；material = Nylon
（补丁后 RealChuteModule 配置）；areaDensity = 0.0000565（MATERIAL 索引）；
ChuteMass = 0.01901424117386341 t；partPrefab.mass = 0.075（补丁后 PART
mass）；massDelta = 0.075000003 + 0.01901424 − 0.075 = **0.01901424 t**。

## ModuleFuelTanks 质量链（IL 证据，修复后重新追踪）

```csharp
// GetModuleMass(defaultMass, sit): massIsAdditive ? mass : mass - defaultMass
// CalculateMass():
//   if (!massDirty) return; massDirty = false;
//   base = basemassConst + basemassPV * volume + MLI 项;      // double
//   if (base < 0) { mass = 0; massIsAdditive = true; }        // blt.un.s
//   else          { mass = (base + Σ tank 质量) * MassMult; }
// CalculateMassRF(ref base): base += MLIArealDensity * totalTankArea
//                            * totalMLILayers;
// get_totalMLILayers() = numberOfMLILayers + get_numberOfAddedMLILayers()
// get_numberOfAddedMLILayers() = (int)_numberOfAddedMLILayers
// ParseBaseMass(string): "A/B" -> basemassPV = A/B, basemassConst = 0
//                        "V"   -> basemassConst = V, basemassPV = 0
```

### massIsAdditive 静态证据链（针对 mk1pod.v2 实例，R2 全链验证）

1. **写入者扫描完整**：1173 个方法体全部读取并完整解码
   （`scan_complete=true`，unparsed=0，decode_incomplete=0）；
   `massIsAdditive` 唯一写入者 = `CalculateMass`（0x06000309，写入 IL 偏移
   0x99，紧邻 `ldc.i4.1` → 常量 true，属于 `base < 0` 分支块；
   `blt.un.s -> 0x8C` 条件为 `basemassConst + basemassPV*volume + MLI < 0`）。
2. **构造函数**：`.ctor`（0x06000348）不写 `massIsAdditive`（CLR 零初始化
   false 是 **CalculateMass 运行前**的初始状态）；`.ctor` 写
   `massDirty = true`（0x085）。
3. **调用路径**：`CalculateMass` 被 `OnLoad`、`OnStart`、`Update`、
   `CleanResources` 调用（调用图扫描完整）；编辑器/飞行加载时 `OnLoad` 置
   `massDirty = true` 后立即调用 `CalculateMass`。
4. **数值证据**：补丁后配置 `basemass = -1`（无 craft 覆盖）→ ParseBaseMass
   IL 形状 `plain_value` → basemassConst = −1.0、basemassPV = 0.0；
   volume = 50.05（craft 状态）。
5. **有效 MLI 项为零（R2 新增，全部为证据而非 None→0）**：
   - craft 状态 `_numberOfAddedMLILayers = 0`（`vab_parser.py` 与
     `ksp_install_index.py` 现在提取原始值与有限数值；补丁后配置无此键）；
   - 基础层 `numberOfMLILayers` 来自 `TankDefinition`（`UpdateTankTypeRF`：
     `ldfld TankDefinition.numberOfMLILayers` 复制）；ServiceModule 的
     TANK_DEFINITION（只读扫描 RealFuels cfg）**没有** numberOfMLILayers 键
     （仅 Cryogenic/BalloonCryo 有 10），且
     `TankDefinition.numberOfMLILayers` 在程序集中**零 IL 写入者**
     （scan_complete），`TankDefinition.Load` 使用
     `ConfigNode.LoadObjectFromConfig`（反射：缺失键不改变字段）→ 保持 CLR
     零初始化 **0**；
   - IL 形状校验通过：`get_totalMLILayers`（ldfld numberOfMLILayers +
     call get_numberOfAddedMLILayers + add）、`get_numberOfAddedMLILayers`
     （ldfld _numberOfAddedMLILayers + conv.i4）、`CalculateMassRF`
     （MLIArealDensity × totalTankArea × get_totalMLILayers + add）；
   - 有效层数 = 0 + 0 = 0 → MLI 项 = 0 × 任意面积/密度 = **0**（已证明，
     `mft_mli_term_evidence` 输出 `proven=true`；任何缺失/非零输入都会使
     MLI 项 UNPROVEN 并退回 insufficient）。
6. **分支执行**：`−1.0 + 0.0·50.05 + 0 < 0` → 零基数分支 → **mass = 0 且
   massIsAdditive = true**（与 craft 保存的 `mass = 0` 一致）。
7. **GetModuleMass**：`massIsAdditive = true` → 返回 `mass` = 0.0；KSP 结算
   部件质量 = 0.4 + 0.0 = **0.4 t**，模块净修正 = **0.0 t**。

结论：**MFT 判为 determined（继续成立）**。`CLR 零初始化 false`（初始状态）
与 `CalculateMass 执行后的有效状态 true` 在证据输出中明确区分
（`clr_zero_init_no_writers` vs `calculate_mass_zero_base_branch`）。

## 符号约定（JSON 与本文档一致）

```text
residual            = estimated_total_mass - loadmeta_totalMass
baseline_residual_t = residual 经 PDC 候选修正后（float32_craft_order
                      − pdc_diff_total − loadmeta totalMass）
predicted_mass_delta_t = 模块语义预测的部件质量修正量（PART-mass modifier）
predicted_residual_t = baseline_residual_t + predicted_mass_delta_t
```

`pdc_diff_total` 缺失时 `baseline_residual_t` 保持缺失（绝不 `or 0.0`）。

## 假设适用性与多组件（R2）

- `applicable` 由**原始 craft** 中的候选部件/模块决定
  （`candidate_part_counts`：parachuteSingle + RealChuteModule；
  mk1pod.v2 + ModuleFuelTanks），与证据组件是否成功生成无关；
- 存在候选部件但证据不完整 → `applicable=true`、`evidence_complete=false`、
  `verdict=insufficient_evidence`；只有确实没有候选部件才是
  `not_applicable`（阴性载具只验证为 not_applicable，绝不 supported）；
- 多个候选组件逐项计算并**求和**（RealChute 各 massDelta 之和、MFT 各
  modifier 之和）；任一适用组件证据缺失时整体不得宣称完整（不取
  `components[0]`）。

## 四个假设的真实结果（三份特殊载具，R2 重新验证）

基线：float32_craft_order 整箭总质量 − PDC 候选差值，与 `.loadmeta.totalMass`
比较，残差 ≈ **+0.07498 t**。

| 假设 | applicable | 证据完整 | predicted_mass_delta_t | predicted_residual_t | 判定 |
| --- | --- | --- | --- | --- | --- |
| parachute_base_mass_only | 是 | 是 | 0 | **+0.07498 t** | contradicted |
| realchute_prefab_modifier | 是 | 是 | +0.01901424 t | **+0.09399 t** | contradicted |
| mk1pod_module_fuel_tanks_modifier | 是 | 是 | 0.0（additive=true，mass=0） | **+0.07498 t** | contradicted |
| combined_realchute_and_mft_semantics | 是 | 是 | +0.01901424 t | **+0.09399 t** | contradicted |

**0.094 t 与 0.019 t 的区分**：

- **RealChute 预测的部件结算质量** = partPrefab.mass + massDelta =
  0.075 + 0.01901424 = **0.09401424 t**；
- **模块 mass delta（modifier）** = massDelta = **+0.01901424 t**；
- **应用于整船基线后的预测残差** = baseline + 0.01901424 = **+0.0940 t**；
- **实际观测残差** = **+0.07498 t**（= 基线）。

combined 假设未把绝对残差压到 5e-5 t 以内（≈+0.094）；未调整符号/常量/容差。

## 阴性对照载具

`Electron I 2112XL.craft`（无 parachuteSingle / mk1pod.v2）：四假设全部
`applicable=false`、`verdict=not_applicable`（候选计数 0/0）；PDC 后 float32
残差 ≈ −2.3e-6 t（5e-5 t 内）。

## 共线性与归因结论

`parachuteSingle` 与 `mk1pod.v2` 完全共线；MFT 已确定（additive=true、
modifier=0）但不能解释残差；RealChute 已由修复后的 IL 再确认但也不能解释。
四假设均无法闭合 +0.075 t 残差：

```text
module_semantics_status = insufficient_evidence
```

剩余归因仍指向 parachuteSingle 相关结算（如结算时刻 partPrefab.mass ≠ 配置
值等无存档证据项）。是否需要受控微型 craft 由下一阶段决定，本阶段**未创建
任何 craft**。

## 工具、确定性与 JSON 输出

- `audit_ksp_mass_residuals.py --json` 输出：
  `module_mass_semantics`（逐载具证据与四假设）、`assembly_provenance`、
  `realchute_il_evidence`、`module_fuel_tanks_il_evidence`；
- **公开 JSON 不再包含完整指令数组**（CalculateMass 等只保留方法 token、
  IL SHA-256、字节数、`il_body_status`/`decode_complete`/`decode_errors`/
  `first_error_offset`、有界摘要、相关字段写入点与有限上下文）；完整指令
  流只在进程内部用于分析；
- 两次运行 JSON 字节一致（确定性）；stdout 仅 JSON；
- 新增/更新测试：`tests/test_ksp_dotnet_il.py`（操作码固定清单一致性 +
  Windows 实时 OpCodes 对照、解码失败封闭、token 归一化、blob 错误、
  scan_complete 语义；纯解析器测试不 skip）、`tests/test_ksp_module_mass_
  semantics.py`（MLI 证据链、扫描不完整阻止判定、候选存在但证据缺失 →
  insufficient、多组件求和、缺失 PDC 保持 insufficient 等）。

## 禁止事项（本阶段已遵守）

未修改生产质量公式 / reconciliation / 正式容差 / `current_part_mass_estimate_t`；
未写 SQL/YAML；未修改 KSP/DLL/cfg/craft/loadmeta；未启动 KSP；未创建受控
craft；未用常量硬编码补齐缺失证据（material、prefab mass、MLI 层数全部来自
索引/craft/IL 证据，缺失即 insufficient）；未把相关性写成权威结论。
