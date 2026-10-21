# KSP 整箭质量 float32 求和顺序取证（阶段 6B.3C.2A）

验证 `Neutrons II 354S.craft` 的剩余异常（double 模型下 PDC 后
`-8.0472788e-5 t`）是否能由 KSP/C# 单精度（IEEE-754 binary32）累加语义
解释。本阶段只增加取证模型，不修改正式质量估算、正式 reconciliation 或
载具数据。

## float32 的严格定义

用 `struct.pack('<f')/unpack('<f')` 模拟 IEEE-754 binary32（不依赖平台
默认浮点）：

```python
to_float32(value)            # binary32 round-trip；bool/NaN/∞/非法/负数 → None
sum_float32_in_order(values) # 累加器=+0.0；每输入先 cast binary32；
                             # 每次加法后立即舍入回 binary32；严格保持顺序；
                             # 任一非法项 → 整体 None（不跳过）
```

`sum_float32_in_order` 与"double 求和后最终 cast"**不等价**（有专门测试证明）。

## 五个候选模型

全部从相同的 `current_part_mass_estimate_t` 出发，只改变整箭聚合语义：

| 模型 | 定义 |
| --- | --- |
| `double_order` | Python double 按 craft PART 顺序累加 |
| `input_float32_then_double` | 每 PART 质量 cast binary32，再 double 累加 |
| `float32_craft_order` | 每输入 cast binary32，按 craft 顺序逐项 binary32 累加 |
| `float32_reverse_order` | 同模型 3，反向 craft 顺序（仅顺序敏感性对照） |
| `double_sum_then_float32` | double 求和后仅最终 cast（证明最终 cast ≠ 逐项单精度） |

每模型输出 `estimated_total_t`、`residual_to_loadmeta_t`、
`residual_after_pdc_t`、`delta_vs_double_order_t`、完整性与诊断。

## PART 顺序必须可审计

float32 求和对顺序敏感，绝不信任 evidence 数组顺序：

- 首选 craft 原始 PART 顺序，同时记录 `craft_index`；
- 每 PART 必须唯一 evidence；missing / duplicate / unexpected / 身份
  不一致 → 模型不完整（`missing_evidence`、`duplicate_evidence_index`、
  `unexpected_evidence_index`、`evidence_identity_mismatch`、
  `duplicate_craft_part_index`、`invalid_mass_value`）；
- 不任意排序部件名、不静默覆盖重复 index；
- 报告保留输入 PART 数、有效质量数、实际使用的 craft_index 顺序、
  完整性状态与异常实例。

## 真实 20 个载具的五模型结果（17 个无 parachuteSingle 载具）

| 模型 | 容差内 | 最大绝对误差 | MAE | RMSE | 最差载具 |
| --- | ---: | ---: | ---: | ---: | --- |
| double_order | 16/17 | 8.0473e-5 | 1.3367e-5 | 2.2068e-5 | Neutrons II 354S |
| input_float32_then_double | 16/17 | 7.5286e-5 | 1.2836e-5 | 2.0897e-5 | Neutrons II 354S |
| **float32_craft_order** | **17/17** | **2.3777e-5** | **7.2038e-6** | **8.8994e-6** | Neutrons II 354S |
| float32_reverse_order | 16/17 | 6.7776e-5 | 1.4851e-5 | 2.3084e-5 | Neutrons II 354S |
| double_sum_then_float32 | 16/17 | 6.7776e-5 | 1.1244e-5 | 1.8915e-5 | Neutrons II 354S |

- `float32_craft_order` 是唯一 17/17 的模型，同时具有最低 MAE 与 RMSE；
- `Neutrons II 354S` 在该模型下 PDC 后残差 **+2.3777188e-5 t**（进入
  5e-5 容差），其余四个模型均不可解释该载具；
- 三个特殊载具在 `float32_craft_order` 下：
  `1_2T Test +0.074984859`、`1_5T Test +0.074984858`、
  `2_5T Test +0.074979457`（均在 0.075 ± 5e-5 内）——float32 模型没有
  破坏已有现象，但不能据此把 0.075 t 正式归因给 parachuteSingle。

## 权威证据核查（只读）

安装目录存在 SSTU 插件源码：
`Source/Plugin/Module/SSTUProceduralDecoupler.cs`（435 行）确认：

```csharp
// updatePhysicalAttributes():
float r = diameter * 0.5f; float h = height; float t = thickness;
innerCylRadius = (r) - (t); outerCylRadius = (r);
innerCylVolume = (float)Math.PI * innerCylRadius * innerCylRadius * h;
outerCylVolume = (float)Math.PI * outerCylRadius * outerCylRadius * h;
volume = outerCylVolume - innerCylVolume;
modifiedMass = volume * massPerCubicMeter;      // 全部 float（binary32）
// GetModuleMass(defaultMass, sit) { return -defaultMass + modifiedMass; }
```

- 环形壳体公式（volume = π(R² − r²)h）与取证候选公式**完全一致**；
- 质量计算链（`(float)Math.PI`、float 乘除、`modifiedMass`、`part.mass`
  ）**全程 binary32**——为 float32 精度假设提供模块级旁证；
- KSP 引擎整箭 `totalMass` 的累加语义（Part.mass 汇总）**没有权威源码**
  （KSP 未开源），只记录"未找到整箭累加的权威证据"，不猜测。

因此：

```text
float32_craft_order 结论强度 = supported_by_dataset
authoritative_ksp_semantics_confirmed = false
production_use_authorized = false
```

样本拟合（17/17）强烈支持"按 craft PART 顺序逐项 float32 累加"，且模块
级源码旁证一致，但**不能**单独证明这是 KSP 官方整箭结算实现，**不得**因此
直接修改生产质量。

## 遗留修复（6B.3C.1-R 收尾）

- 零质量候选合法：`candidate_mass_t is None` 显式判断（thickness=0 或
  massPerCubicMeter=0 是合法零质量候选，不再是"证据缺失"）；
- 全局 index 守恒：craft 全部 PART 重复 index / evidence 的 unexpected
  index / part/base_part/persistent_id 身份冲突 / 同一 PART 多个不同 PDC
  模块状态（`ambiguous_pdc_module_state`，不选第一条；全等则采用并记录
  来源数）均使候选不完整；
- CLI：`--json` 文本模式遇 `residual_after_pdc_t=None` 显示
  `None/证据不足`（不执行 abs(None)），UTF-8 断言精确到
  `自动存档飞船.craft`，JSON 两次运行字节完全一致，stdout 仅 JSON。

## 样本完整性契约（6B.3C.2A-R）

缺失样本**绝不**从分母中删除：

```text
all_samples_complete =
    expected_sample_count > 0
    and valid_sample_count == expected_sample_count
    and incomplete_sample_count == 0

all_within_tolerance =
    all_samples_complete
    and within_tolerance_count == expected_sample_count
```

每个模型输出 `expected_sample_count`、`valid_sample_count`、
`incomplete_sample_count`、`incomplete_vehicles`（保留文件名与稳定原因）、
`within_tolerance_count`、`all_samples_complete`、`all_within_tolerance`。
即使 16/17 全部进入容差，只要 1 个样本不完整，
`all_within_tolerance=false`，不得宣称全部通过。

## 空 craft 与零质量 PART 的区别

- 空 craft（`parts=[]`）：`complete=false`，五模型 total/residual 全部
  null，diagnostics 含 `empty_craft`——"没有 PART"≠"零质量"；
- 单个合法 `0.0 t` PART：`complete=true`、`estimated_total_t=0.0`——
  零质量 PART 合法参与计算；`-0.0` 规范化为 `+0.0`。

## binary32 溢出行为

- `to_float32(1e100)` 不抛未处理异常（捕获 struct OverflowError），返回
  不可用状态；绝不产生 Infinity；
- 诊断层区分：`invalid_float32_input`（bool/NaN/∞/负/非数字）、
  `float32_overflow`（输入超出 binary32 范围）、
  `float32_accumulation_overflow`（逐项累加中途溢出）——溢出不是普通
  缺失值，使该模型不完整（其他模型照常计算）；
- CLI 不因溢出输出 traceback。

## 取证容差验证

`fleet_model_comparison(tolerance_t=...)` 拒绝 bool、NaN、Infinity、
字符串垃圾、负数与零；非法容差 → 整体 `status='invalid_input'` +
`invalid_tolerance` 诊断，`tolerance_t=null`，**不**产生任何支持性结论，
也不静默采用默认值。

## 机器可读结论（动态计算，不硬编码）

```json
{
  "conclusion": {
    "preferred_candidate": "float32_craft_order",
    "conclusion_strength": "supported_by_dataset",
    "authoritative_ksp_semantics_confirmed": false,
    "production_use_authorized": false,
    "all_required_samples_complete": true,
    "selection_reasons": ["all_normal_samples_within_tolerance",
                          "lowest_mae", "lowest_rmse"]
  }
}
```

`supported_by_dataset` 仅当同时满足：普通载具样本非空、该模型所有普通
样本证据完整且进入取证容差、该模型具有**严格最低** MAE 与**严格最低**
RMSE、不存在并列最优。否则 `preferred_candidate=null`、
`conclusion_strength=insufficient_evidence`、`production_use_authorized=
false`。无论数据多吻合，`authoritative_ksp_semantics_confirmed` 恒为
`false`——**`supported_by_dataset` 不等于生产授权**。结论嵌入
`fleet_numeric_comparison.conclusion`（CLI JSON 唯一来源，无重复副本）。

## 真实 fleet 结论（本机 20 载具）

- `float32_craft_order`：expected=17 / valid=17 / incomplete=0 /
  within=17 / all_samples_complete=true / all_within_tolerance=true；
  最大绝对误差 2.3777e-5 t、MAE 7.2038e-6 t、RMSE 8.8994e-6 t（全部
  模型最低）；
- conclusion：`preferred_candidate=float32_craft_order`、
  `conclusion_strength=supported_by_dataset`、
  `authoritative_ksp_semantics_confirmed=false`、
  `production_use_authorized=false`；
- `Neutrons II 354S` PDC 后残差 +2.3777188e-5 t（进入 5e-5 容差）；
- 其余四模型 16/17。

## 下一阶段建议

- **6B.3C.2B 权威运行语义验证**：基于已找到的 SSTU 源码逐模块模拟 float
  结算链（含 `(float)Math.PI` 的运算顺序），并调查 KSP 引擎 `totalMass`
  累加（若可经 mod 日志/配置获得旁证）；
- 或开展 `parachuteSingle/mk1pod.v2` 受控解耦取证。
