# KSP 受控质量实验 — 正式差分分析结果（attempt-002）

> 历史记录：本文引用的 `experiments/control_mass/attempts/attempt-002/`
> 原始文件已在 2026-10-21 清零，不能作为当前证据或重新授权依据。

> 状态：`analyzed`（2026-08-02，KSP 指纹下实验支持 `realchute_settled_mass`）。
> 本文档由 `attempt-002/result.json`（SHA-256 `2c1433b7183614385615dd65b6d59bf8be716c825c49b9b9ffbee24ab755e178`）与 `attempt-002/capture_receipt.json` 生成；除格式外未修改任何数值。

## 1. 证据沿袭：attempt-001 → attempt-002

- attempt-001（2026-08-02）：首次真实采集，`invalid_design`，唯一 diff 为 `chute_calculation_mode_mismatch`（M2 两把伞 `calcSelect=True`，Automatic）。
- attempt-002：用户按清单将 M2 两把伞切换为 Manual（`calcSelect=False`）并重新保存；M0/M1/M3 六个文件从 attempt-001 不可变归档继承，M2 两个文件来自用户重新保存的 KSP VAB 目录（只读复制）。
- attempt-001 与 canonical `input/`、原 `capture_receipt.json`、原 `result.json` 均未改写；attempt-001 路径元数据更正记录见 `attempts/attempt-001/evidence_hashes_correction.json`。

## 2. 为什么重新采集 M2

attempt-001 中 M2 两个降落伞被保存为 **Automatic** 计算模式（`calcSelect=True`），与 M1/M3 的 Manual（`calcSelect=False`）不一致，导致 M1/M2 设计状态不等、分析被判定为 `invalid_design`。用户按要求只重新保存了 M2，未重做 M0/M1/M3。

## 3. 八个文件 SHA-256

| 文件 | 来源 | SHA-256 |
| --- | --- | --- |
| `M0_control.craft` | inherited_from_attempt-001 | `c54e4e90564bf6e92582a75fa3cb5d87506592493b698d1cb9255870026fc3b6` |
| `M0_control.loadmeta` | inherited_from_attempt-001 | `66820d4f78fe366a8380905c152f0abbb1101078a9bcb42d6c4b04c9ea5b09de` |
| `M1_one_chute_default.craft` | inherited_from_attempt-001 | `58cb4088e2a2c4512b5ddb2727a16164905c652f2a1a353ebe2429791de2addb` |
| `M1_one_chute_default.loadmeta` | inherited_from_attempt-001 | `20979b3aa84f57acef4ba37a6585d443841de368d80ba4c4b2148d61b9357e52` |
| `M2_two_chutes_default.craft` | new_user_recapture | `c1f6a9b6861ffaae00594537a13c0cf3c934e4bbef6fb074251c3aab9ed07df6` |
| `M2_two_chutes_default.loadmeta` | new_user_recapture | `5ffd208c2037ea91be4be57c7303777b0e7aec60a9455ef7d5755f9a849064d2` |
| `M3_one_chute_changed_diameter.craft` | inherited_from_attempt-001 | `8462e474ea747ad499ce4e72b87779876071e3c52f54778d46cb92635d6d5a7e` |
| `M3_one_chute_changed_diameter.loadmeta` | inherited_from_attempt-001 | `203da0a6368cc7ed44f36716fb5933ecc8140d2b21bb45f1ccbaa9d58ed428a2` |

新 M2 源文件：craft `c1f6a9b6861ffaae00594537a13c0cf3c934e4bbef6fb074251c3aab9ed07df6`（大小 30593 B，mtime 2026-08-02T23:13:53.101285+08:00）；loadmeta `5ffd208c2037ea91be4be57c7303777b0e7aec60a9455ef7d5755f9a849064d2`。旧 M2（attempt-001）哈希：craft `6c847435deab67b1f13b2fa7278e704193a31110726611245cb0dfba1866477e`、loadmeta `b514025742b65977a87b666f22395c65dfac66bad899ae3970c00b3cb615de56`（已被替换）。

## 4. KSP 与 DLL 指纹

- ConfigCache：`15c6d8dd36fd9310c964d67f0172906311956e19824ae38f66a1269a2bccaff3`
- RealChute.dll：`884acb3f4537a2164fa4b6d798be538c6fed54997c5de0a7674527bc973b56d5`
- RealFuels.dll：`6c6629ed95955f4f0bd88041319444da45115223415bdc20febdb04a33377b5b`
- 与 manifest 指纹一致（analyzer `fingerprint_mismatch` 未触发）。

## 5. 四组原始及 binary32 质量（loadmeta totalMass）

| 场景 | raw 十进制 | parsed 十进制 (t) | binary32 存储值 (t) | 量化界限 (t) |
| --- | --- | --- | --- | --- |
| M0_control | `0.0399999991` | 0.0399999991 | 0.03999999910593033 | 1.862645149230957e-09 |
| M1_one_chute_default | `0.134014249` | 0.134014249 | 0.13401424884796143 | 7.450580596923828e-09 |
| M2_two_chutes_default | `0.228028491` | 0.228028491 | 0.22802849113941193 | 7.450580596923828e-09 |
| M3_one_chute_changed_diameter | `0.142734379` | 0.142734379 | 0.1427343785762787 | 7.450580596923828e-09 |

## 6. 三组差分

- M1−M0（单伞实际增量）：`0.0940142497420311` t
- M2−M0（双伞实际增量）：`0.1880284920334816` t
- M3−M1（直径 20.7→25.0 m 的质量效应）：`0.00872012972831726` t

## 7. 线性检查（M2−2·M1+M0）

- 观测 `2×(M1−M0)=2×0.0940142497420311` → 预测双伞 `0.1880284994840622` t
- 实际 `M2−M0=0.1880284920334816` t；绝对误差 `7.450580596923828e-09` t
- 观测误差界 `2.421438694000244e-08` t；判定容差 `1.2421438694000244e-07` t
- **verdict：`supported`**（within_tolerance=True）

## 8. 直径检查（M3−M1 vs RealChute chute mass 预测）

- 预测 chute mass 增量（20.7→25.0 m，Nylon，密度 5.65e-05 t/m²·unit）：`0.00872013159096241` t
- 观测直径效应：`0.00872012972831726` t；绝对误差 `1.862645149230957e-09` t
- 判定容差 `1.1905924396083946e-07` t；**verdict：`supported`**

## 9. runtime modMass 诊断（part-level，KSP 持久化的派生质量修正）

| 场景 | modMass raw | parsed (t) | binary32 零噪声界内 | 参与设计校验 |
| --- | --- | --- | --- | --- |
| M0_control 核心 | — | 0.0 | — | 参与 |
| M1_one_chute_default 核心 | — | 0.0 | — | 参与 |
| M1_one_chute_default 降落伞 | `3.7252903E-09` | 3.7252903e-09 | True | 否 |
| M2_two_chutes_default 核心 | — | 0.0 | — | 参与 |
| M2_two_chutes_default 降落伞 | `3.7252903E-09` | 3.7252903e-09 | True | 否 |
| M2_two_chutes_default 降落伞 | `3.7252903E-09` | 3.7252903e-09 | True | 否 |
| M3_one_chute_changed_diameter 核心 | — | 0.0 | — | 参与 |
| M3_one_chute_changed_diameter 降落伞 | `0.00872013345` | 0.00872013345 | False | 否 |

- 零噪声界限（binary32 ULP @ 0.094014 t 参考尺度）：`7.450580596923828e-09` t（无固定 epsilon）。
- 直径质量预测（chute mass 增量）与 M3 的 modMass（`0.00872013345` t）一致：预测 `0.00872013159096241` t。M1/M2 的 `3.7252903E-09` 为 float32 噪声。

## 10. 三个候选模型：预测、误差、容差与 verdict

| 模型 | 单伞贡献 | 预测增量 (t) | 观测增量 (t) | 绝对误差 (t) | 判定容差 (t) | verdict |
| --- | --- | --- | --- | --- | --- | --- |
| `prefab_base_only` | 单个降落伞的贡献 = 补丁后 PART mass | 0.075 | 0.0940142497420311 | 0.0190142497420311 | 1.4507601261138916e-07 | **contradicted** |
| `realchute_settled_mass` | 单个降落伞的贡献 = caseMass + ChuteMass | 0.09401424303650856 | 0.0940142497420311 | 6.705522540007003e-09 | 1.5414271022630146e-07 | **supported** |
| `part_effectively_excluded` | 单个降落伞的贡献 ≈ 0 | 0.0 | 0.0940142497420311 | 0.0940142497420311 | 1.0931322574615478e-07 | **contradicted** |

## 11. 最终结论

- 三个候选模型均 `evidence_complete=true`；其中 **仅 `realchute_settled_mass` 得到 `supported`**（单伞贡献 = caseMass + ChuteMass = `0.075000003 + 0.01901424 ≈ 0.09401424` t，与观测 `0.09401425` t 之差 `6.7e-9` t，远小于容差 `1.54e-7` t），`prefab_base_only` 与 `part_effectively_excluded` 均 `contradicted`。
- 线性检查（M2−2·M1+M0）与直径检查（M3−M1）均 `supported`，与单模型支持一致，无歧义。
- **结论：在当前 KSP 指纹（ConfigCache `15c6d8dd36fd9310…`、RealChute `884acb3f4537a216…`、RealFuels `6c6629ed95955f4f…`）下，实验支持 `realchute_settled_mass`：parachuteSingle 的实际质量贡献为 RealChute 的 `caseMass + ChuteMass`，且 ChuteMass 随展开直径按面积密度缩放。**
- `prefab_base_only`（仅补丁后 PART mass）与 `part_effectively_excluded`（≈0）均被该指纹下的真实数据反驳。

## 12. 适用范围与未解决问题

- 适用：本实验的载具组合（OKTO2 控制核心 + Mk16 降落伞，Nylon，RealChute `caseMass=0.075000003`，直径 20.7/25.0 m）、上述 KSP 指纹、binary32 序列化模型 `ieee754_binary32_round_trip`。
- 未解决：仅覆盖一种材料（Nylon）与一种伞型（parachuteSingle）；其他材料/直径范围、其他 chute 部件、RealChute 其他版本、不同核心组合下的验证不在本实验范围。
- 本实验的差分（M1−M0、M2−M0、M3−M1）打破了对总质量的绝对拟合；绝对质量仅用于诊断。

## 13. 是否具备进入生产质量模型修改阶段的证据

**是（有条件）。** `evidence_complete=true`、唯一完整模型 `realchute_settled_mass` supported 且其余完整模型 contradicted，线性与直径检查通过；证据链（craft/loadmeta 哈希、指纹、设计状态、连接拓扑）全部验证。进入生产质量模型修改阶段时仍需：保持本 attempt 证据不可变、对 `server/ksp_module_mass_semantics.py` 的对应语义表做最小化修订并通过全量回归（当前 861 passed）。

*本文档仅报告实验证据与分析器 verdict；按阶段规则，本阶段不修改任何生产质量逻辑。*
