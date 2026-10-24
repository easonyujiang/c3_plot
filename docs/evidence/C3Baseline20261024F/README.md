# 基线飞行证据 · C3Baseline20261024F

**状态：`target_verified`。** 这是一条真实游戏飞行记录，不是模型输出。它证明
1.000 t 载荷被送入 220 × 220 km、20° 的目标轨道并保持 30 s；它不证明这枚
火箭的最大运力，也不构成重复性统计。

- 飞行窗口：2026-10-24 21:19–21:27（Asia/Shanghai），即
  13:19:26–13:27:41 UTC
- 游戏：KSP 1.12.5.3190，RSS/FAR/RealFuels/SSTU/MechJeb 2.14.2 等
- 载具：`C3 Neutrons I 2630 Payload Baseline`
- 载具来源：从 `saves/RocketMaking` 的 `Neutrons I 2630` 与 `1T_Test`
  载荷捐赠件组合出的隔离存档，未修改原始存档
- 飞行程序：MechJeb 2.14.2 PVG，固定 `PitchStartVelocity=50`、
  `PitchRate=0.5`、目标 220 km / 20°，自动分级，`skipCircularization=true`

## 结果

| 项目 | 值 |
| --- | --- |
| 交付载荷 | 1.000 t（11 个部件，含铅配重质量模拟件） |
| 目标轨道 | 220 × 220 km，20° |
| 最终近地点 | 219.801 km |
| 最终远地点 | 220.045 km |
| 最终倾角 | 20.00008° |
| 目标保持 | 30.62 s |
| 遥测样本 | 416 |
| 最终载具质量 | 5.706453 t |
| 最终剩余级 | 第 1 级（上面级） |
| 整流罩 | 发射台上受保护；入轨后已抛弃 |
| 载荷完整性 | 11/11 部件，可控 |

分级事件（遥测时间）：

| 相对时间 / s | 级 | 高度 / km | 质量 / t |
| ---: | ---: | ---: | ---: |
| 0.04 | 5 | 0.136 | 152.879 |
| 5.14 | 4 | 0.136 | 152.879 |
| 6.18 | 3 | 0.137 | 151.709 |
| 187.78 | 2 | 117.285 | 26.466 |
| 188.80 | 1 | 118.277 | 20.411 |

入轨后最终质量 5.706 t 中包含未耗尽推进剂。**剩余 Δv 必须由模型根据末态
质量和级参数推导，不能把最终轨道本身当作剩余运力。**

## 已知飞行程序问题：非预期滑行

一级在 **171.46 s 燃尽**（推力 0，质量 26.495 t，stage 3），但上面级直到
**188.80 s** 才点火（stage 1，质量 20.411 t，推力 213 kN）。中间有
**17.34 s** 的推力为零滑行；`throttle` 仍为 1.0，`stage` 停在 3。
`flight.csv` 第 170–187 行完整记录；stage 2 在 187.78 s，stage 1 在
188.80 s。

这是**非预期滑行**，不是文档化的标准飞行程序。该架次仍可证明 1.000 t 被
送达目标轨道，但在修正分级前，它的载荷/Δv 只能作为**受该程序惩罚的下界**，
不应作为干净、可比较的标准程序校准值。

## 来源与指纹

- 飞行 craft：`D18E0502D35A8DFA3418114BFE85D35B79513D0E37EA66EF45BBE1D9739194FE`
- 准备输入 craft：`BADA007C94D06B94442D98F362F0DCBD3F7B230E807FE0517B5A325C29744763`
  （编辑器重建整流罩后被实际飞行版本替换）
- 源存档 `persistent.sfs`：`423D2ECF553033E26E02571477AA4C6EBACED8ACB7F83A9D973C3BA44B11841`
- `ModuleManager.ConfigCache`：`15C6D8DD36FD9310C964D67F0172906311956E19824AE38F66A1269A2BCCAFF3`
- `MechJeb2.dll`：`277A285D4212AB594E157F03B46DE408B283E03CAB0518C4EC312C4C6AF91253B`
- `C3MassValidation.dll`：`15427032CF0AC3ACAF6842A115DBBA3AE44479A5E0C6675A9F5908E0EF301908A`
- 78 个源文件与 3 个环境文件在审计时均未变化。

准备输入的载荷在发射台上实测为 1.223194857 t。运行时关闭 4 个
`RefuelingPump` 并调整 `LeadBallast` 后，载荷被精确结算到
0.99999996 t；这些步骤记录在 `harness-status.txt` 和
`before_ballast.c3inspection` 中。

## 如何复核

只读复核需要本机 KSP 安装：

```bash
.venv/bin/python -m tools.game.audit_baseline \
  --ksp-root "/mnt/d/SteamLibrary/steamapps/common/Kerbal Space Program" \
  --folder C3Baseline20261024F
```

把本证据包重新从游戏现场冻结：

```bash
.venv/bin/python -m tools.game.freeze_baseline_evidence \
  --ksp-root "/mnt/d/SteamLibrary/steamapps/common/Kerbal Space Program" \
  --folder C3Baseline20261024F
```

`freeze_baseline_evidence` 只写入 `docs/evidence/`，不会写游戏存档；默认拒绝
覆盖已有证据包，需显式 `--force`。

## 边界

- 一次飞行，一个载荷点；不能据此宣称最大运力、成功率或重复性。
- 本证据不改变 C3 曲线；把 1 t 点转成校准点需要单独的质量/Δv 推导和复核。
- 固定飞行程序的结果；更换 PVG 参数、载荷形状或环境后需要重新飞行。
- 最终质量包含剩余推进剂；不能把“仍在轨”误读为“已用尽运力”。
- 证据包中的 craft 是可复核的飞行输入，不是可复用的业务样例。

## 文件

| 文件 | 作用 |
| --- | --- |
| `flight.csv` | 416 行飞行遥测 |
| `C3Baseline20261024F.c3inspection` | 发射台初始快照 |
| `before_ballast.c3inspection` | 配重调整前快照 |
| `configured.c3inspection` | 起飞前 MechJeb/PVG 配置快照 |
| `final.c3inspection` | 入轨后末态快照 |
| `prepared-inputs.json` | 准备阶段的源文件与哈希清单 |
| `editor-snapshot.c3snapshot` | 编辑器结算质量与 Mod 指纹 |
| `flown-craft.craft` | 实际飞行 craft |
| `harness-status.txt` | 本次飞行连续的 harness 状态日志 |
| `audit.json` | `audit_baseline` 的完整输出 |
| `manifest.json` | 机器可读摘要与每个文件的 SHA-256 |
