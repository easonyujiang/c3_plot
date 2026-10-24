# RealChute 结算质量接入生产证据链 — 启用文档（6B.3C.4A）

> 生产门控仍按固定配置/DLL 指纹 fail-closed。原始
> `experiments/control_mass/attempts/attempt-002/result.json` 已在
> 2026-10-21 清零，本文不能用于环境变化后的重新授权。

> 历史证据（原始文件已清零）：`experiments/control_mass/attempts/attempt-002/result.json`
> （SHA-256 `2c1433b7183614385615dd65b6d59bf8be716c825c49b9b9ffbee24ab755e178`，
> `experiment_status=analyzed`，`validation.diffs=[]`）。
> 本阶段不修改生产质量模型本身；本文档说明已将已验证结论接入
> .craft 导入预览的逐部件质量证据链与整箭对账，并定义生产授权门。

## 1. attempt-002 支持的结论

受控实验中唯一 `supported` 的完整模型为 **`realchute_settled_mass`**：

```text
settled_part_mass = caseMass + ΣChuteMass
ChuteMass         = DeployedArea × material.AreaDensity
DeployedArea      = conv.r4(pi × deployedDiameter² / 4)
（与 RealChute 1.4.9.5 IL 一致：float32 累加、binary32 运算顺序）
```

- `prefab_base_only`（仅补丁后 PART mass）与 `part_effectively_excluded`（≈0）
  均被该指纹下的真实数据 `contradicted`；线性检查（M2−2·M1+M0）与直径检查
  （M3−M1）均 `supported`，无歧义。

## 2. settled part mass 与 runtime modMass 的区别

- **settled part mass**：KSP 运行时实际结算出的部件质量（caseMass + ChuteMass），
  是生产证据链使用的权威值。
- **saved modMass**：KSP/PartModule 持久化到 craft 的**派生质量修正**
  （= settled − ConfigCache prefab 的相对量），不是用户直接设置的设计字段：
  - M1/M2 的 `3.7252903E-09` 是 binary32 零噪声；
  - M3 的 `0.00872013345` 只反映相对默认直径的运行时变化。
- 因此 `ConfigCache base_mass + saved modMass` 会漏掉默认 ChuteMass，已
  不再作为 parachuteSingle 最终质量的权威来源；saved modMass 只进入
  诊断字段（`mass_semantics_evidence.saved_mod_mass_t` 与实例 diagnostics）。

## 3. 生产授权范围（结构化记录）

授权记录：`server/ksp_module_mass_semantics.py::REALCHUTE_PRODUCTION_AUTHORIZATION`
（`authorization_id = realchute_settled_mass_v1`），包含 attempt-002 result
SHA-256 与指纹。仅当全部满足时使用 settled mass：

| 维度 | 授权值 |
| --- | --- |
| canonical part | `parachuteSingle` |
| ConfigCache SHA-256 | `15c6d8dd36fd9310c964d67f0172906311956e19824ae38f66a1269a2bccaff3` |
| RealChute.dll SHA-256 | `884acb3f4537a2164fa4b6d798be538c6fed54997c5de0a7674527bc973b56d5` |
| RealChuteModule / ProceduralChute | 各唯一且完整（歧义即失败） |
| material | `Nylon` |
| 计算模式 | Manual（`calcSelect=False`） |
| getMass / useDry | `True` / `True` |
| chuteCount | 1；每部件仅一个有效 chute |
| 展开直径 | 20.7–25.0 m（闭区间，含两端） |
| caseMass / 直径 / 材料 / areaDensity | 全部来自 craft/ConfigCache 证据，禁止常量回退 |

## 4. 指纹失配与证据不足时的 fail-closed 行为

`server/ksp_mass_evidence.py` 对授权失败一律返回
`resolution_status=dynamic_unresolved`、`current_part_mass_estimate_t=None`，
稳定诊断码：

| 情形 | 状态 | 诊断码 |
| --- | --- | --- |
| ConfigCache / RealChute.dll 指纹缺失 | dynamic_unresolved | `realchute_fingerprint_missing` |
| 指纹不匹配 | dynamic_unresolved | `realchute_fingerprint_mismatch` |
| 模块状态歧义（多份 RealChuteModule/ProceduralChute） | dynamic_unresolved | `realchute_module_state_ambiguous` |
| caseMass / material / areaDensity / diameter 缺失或非法 | dynamic_unresolved | `realchute_evidence_incomplete` |
| saved modMass 显式非法 | dynamic_unresolved | `realchute_saved_mod_mass_invalid` |
| 超出授权范围（材料/模式/chute 数/直径/部件） | unsupported_domain → dynamic_unresolved | `realchute_scope_unsupported` |

绝不退回 ConfigCache base mass，也绝不继续使用 base + saved modMass。
DLL 指纹由 `server/ksp_runtime_fingerprints.py` 按（路径, 大小, mtime_ns）
进程内缓存提供；`import_service` 每次请求读取一次并作为 provenance 传入
`extract_part_mass_evidence`，逐部件纯函数不做任何磁盘扫描。

## 5. 尚未授权的 RealChute 范围

- 其他 RealChute 部件（非 parachuteSingle）；
- 其他材料（非 Nylon）；
- Automatic 模式（calcSelect=True）——旧 VAB 特制载具的降落伞即属此类，
  生产证据链对其正确失败关闭（`dynamic_unresolved`，整箭对账
  `evidence_incomplete`）；
- 多 canopy / 多 chute 单部件；
- 授权区间外的展开直径；
- 其他 RealChute.dll 或 ConfigCache 指纹。

## 6. attempt-002 四组端到端对账结果

（真实索引 + 真实指纹 provenance，严格容差：绝对 1e-6 t / 相对 1e-7，未修改）

| 场景 | parachute 实例 | 结算质量 (t) | include_all 残差 (t) | 容差 (t) | 对账状态 |
| --- | --- | --- | --- | --- | --- |
| M0_control | 0 | — | +9.0e-10 | 1e-6 | ambiguous_scenario |
| M1_one_chute_default | 1 | 0.0940142422914505 | −6.7e-9 | 1e-6 | ambiguous_scenario |
| M2_two_chutes_default | 2 | 2 × 0.0940142422914505 | −6.4e-9 | 1e-6 | ambiguous_scenario |
| M3_one_chute_changed_diameter | 1 | 0.10273437201976776 | −7.0e-9 | 1e-6 | ambiguous_scenario |

（`ambiguous_scenario` 表示 include_all 与排除非飞行候选两个口径同时匹配——
这些载具没有非飞行候选部件，属既有对账语义，未修改。）

- 预览仍为 `needs_review`（craft 草稿恒带 `requires_mass_confirmation=True`）；
- `stage.wet_mass_t` / `stage.dry_mass_t` 不被自动填写（placeholder 级 0.0）；
- 本阶段不产生任何 SQL/YAML 写入；
- attempt-001/attempt-002 证据文件均只读，未被复制、重写或格式化。

## 7. 结论

已验证的 `realchute_settled_mass` 已按严格授权门接入生产逐部件质量证据链与
整箭质量对账；M1/M2/M3 的真实对账残差约 1e-8 t 量级，远在既有严格容差内。
saved modMass 降级为诊断证据。超出授权范围时 fail closed，不估算、不猜测。
