# 基线飞行证据 · C3Baseline20261024G

**状态：`target_verified`。** 这是第二条真实游戏飞行记录：同一枚
`C3 Neutrons I 2630 Payload Baseline` 把 **2.500 t** 载荷送入
**220 × 220 km、20°** 目标轨道并保持 30 s。

- 飞行窗口：2026-10-24 23:04–23:13（Asia/Shanghai），即
  15:04:50–15:13:21 UTC
- 游戏：KSP 1.12.5.3190，RSS/FAR/RealFuels/SSTU/MechJeb 2.14.2 等
- 载具来源：从 `saves/RocketMaking` 的 `Neutrons I 2630` 与 `1T_Test`
  载荷捐赠件组合；原始存档未修改
- 飞行程序：MechJeb 2.14.2 PVG，固定 `PitchStartVelocity=50`、
  `PitchRate=0.5`、目标 220 km / 20°，自动分级，`skipCircularization=true`

## 结果

| 项目 | 值 |
| --- | --- |
| 目标载荷 | 2.500 t |
| 实测载荷 | 2.4999999 t（11 个部件） |
| 目标轨道 | 220 × 220 km，20° |
| 最终近地点 | 219.006 km |
| 最终远地点 | 220.031 km |
| 最终倾角 | 20.00010° |
| 目标保持 | 30.60 s |
| 遥测样本 | 427 |
| 最终载具质量 | 5.854024 t |
| 最终剩余级 | 第 1 级（上面级） |
| 整流罩 | 发射台上受保护；入轨后已抛弃 |
| 载荷完整性 | 11/11 部件，可控 |

分级事件（遥测时间）：

| 相对时间 / s | 级 | 高度 / km | 质量 / t |
| ---: | ---: | ---: | ---: |
| 0.04 | 5 | 0.137 | 154.379 |
| 5.14 | 4 | 0.137 | 154.379 |
| 6.18 | 3 | 0.138 | 153.209 |
| 181.64 | 2 | 108.920 | 21.978 |
| 189.80 | 1 | 116.885 | 21.358 |

## 已知飞行程序问题：非预期滑行

一级在 **170.40 s 燃尽**（推力 0，质量 27.995 t，stage 3），上面级直到
**181.64 s** 才点火（stage 2，质量 21.978 t，推力 213 kN）。中间有
**11.24 s** 的推力为零滑行；`throttle` 仍为 1.0，`stage` 停在 3。
`flight.csv` 第 169–180 行完整记录；stage 1 在 189.80 s（质量降到
21.358 t，伴随整流罩/次级事件）。

这次 2.500 t 仍为 `target_verified`，但同样不是干净的标准程序。F 与 G 的
滑行长度不同（17.34 s vs 11.24 s），所以**两个点不是同一有效上升程序下的
可比测量**；在修正分级前，它们只能作为受该程序惩罚的下界。

## 载荷模拟件

这次仍使用现有 payload 舱内的铅配重，外形和整流罩与 1 t 基线相同；
它是**质量模拟件**，不是真实载荷体积/形状。

- 初始配重：250 units（payload 舱约 3.005 t）
- 运行时关闭 4 个 `RefuelingPump`，配重调整到 **205.46736979 units**
- 调整后载荷：**2.4999999 t**

## 来源与指纹

- 飞行 craft：`0BD9E9931AB38496E4065B465A6E6AEE15880028FD3B46739A74AB4BC4462F77`
- 准备输入 craft：`1D8DBA223E707BB45063F02D200CA93F63170B6A86B568FE764A02412B4FC1AB`
  （编辑器重建整流罩后被实际飞行版本替换）
- 源存档 `persistent.sfs`：`423D2ECF553033E26E02571477AA4C6EBACED8ACB7F83A9D973C3BA44B11841`
- `ModuleManager.ConfigCache`：`15C6D8DD36FD9310C964D67F0172906311956E19824AE38F66A1269A2BCCAFF3`
- `MechJeb2.dll`：`277A285D4212AB594E157F03B46DE408B283E03CAB0518C4EC312C4C6AF91253B`
- `C3MassValidation.dll`：`7A1E697B538AD6B13C623BC09C53814E26CC4ECA1DEE6C4EA0F6E6955597E2AA`
- 78 个源文件与 3 个环境文件在审计时均未变化。

## 校准候选值

按上面级干重 **0.889261 t**、真空比冲 **300 s** 和末态质量估算，两条
已验证飞行对应的剩余 Δv 约为：

| 载荷 / t | 末态质量 / t | 剩余推进剂 / t | 估算 `dv_rem` / m·s⁻¹ |
| ---: | ---: | ---: | ---: |
| 1.000 | 5.706453 | 3.817193 | 3252.1 |
| 2.500 | 5.854024 | 2.464763 | 1607.9 |

这是用于校准的**模型推导值**，不是遥测直接读数；正式写入 C3 校准前仍需
按 `compute_c3_mass` 的质量/Δv 定义复核。它与历史 2.5 t 校准点
（`dv_rem ≈ 1581 m/s`）量级一致。

## 如何复核

```bash
.venv/bin/python -m tools.game.audit_baseline \
  --ksp-root "/mnt/d/SteamLibrary/steamapps/common/Kerbal Space Program" \
  --folder C3Baseline20261024G
```

把本证据包重新从游戏现场冻结：

```bash
.venv/bin/python -m tools.game.freeze_baseline_evidence \
  --ksp-root "/mnt/d/SteamLibrary/steamapps/common/Kerbal Space Program" \
  --folder C3Baseline20261024G
```

## 边界

- 一次飞行，一个载荷点；不能据此宣称最大运力、成功率或重复性。
- 载荷是现有 payload 舱内的质量模拟件；未验证真实载荷体积、形状和安装方式。
- 本证据不改变 C3 曲线；1 t 与 2.5 t 两个点还不足以确定三点校准。
- 固定飞行程序的结果；更换 PVG 参数、载荷形状或环境后需要重新飞行。
- 最终质量包含剩余推进剂；剩余 Δv 的推导值需按模型定义复核。

## 文件

| 文件 | 作用 |
| --- | --- |
| `flight.csv` | 427 行飞行遥测 |
| `C3Baseline20261024G.c3inspection` | 发射台初始快照 |
| `before_ballast.c3inspection` | 配重调整前快照 |
| `configured.c3inspection` | 起飞前 MechJeb/PVG 配置快照 |
| `final.c3inspection` | 入轨后末态快照 |
| `prepared-inputs.json` | 准备阶段的源文件、载荷目标与哈希清单 |
| `editor-snapshot.c3snapshot` | 编辑器结算质量与 Mod 指纹 |
| `flown-craft.craft` | 实际飞行 craft |
| `harness-status.txt` | 本次飞行连续的 harness 状态日志 |
| `audit.json` | `audit_baseline` 的完整输出 |
| `manifest.json` | 机器可读摘要与每个文件的 SHA-256 |
