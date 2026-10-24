# 火箭 Profile 与参数目录（Phase A 初版）

更新：2026-10-24。对应 [产品路线图](../PRODUCT_ROADMAP.md) 的 Phase A。当前只生成**参数档案**，不计算运力、不写数据库、不自动标定。

## 命令

```bash
.venv/bin/python -m tools.profile \
  --craft "<craft 路径>" \
  --ksp-root "<KSP 根目录>" \
  --payload-parts "probeCoreOcto.v2,batteryPack,proceduralTankRealFuels,longAntenna" \
  --output output/profiles/<名称>.json
```

- `--payload-parts` 可选；按部件**名称**把载荷从级段中显式声明出来。不声明时 `payload.status=unknown`，级段质量仍按编辑器分组给出。
- 输出默认在 `output/profiles/`（Git 忽略）。
- 只读：读取 craft、GameData/ConfigCache、编辑器快照；不修改游戏或数据库。

## 实际示例

对 `C3Baseline20261024H` 的 craft + 编辑器快照：

- 2 个级段、24 个部件、117 个参数；
- `payload.status=declared`，载荷 1.223194857 t（11 个部件）；
- 整流罩侧壁 0.029188927 t，`fairings.state=present`；
- 级段质量来自编辑器快照，`mass_scope=editor_grouping`，并用
  `payload_included` / `fairing_included` 标记是否包含载荷/整流罩；
- 环境：craft 版本 1.12.5、ConfigCache/MechJeb/Validation DLL 哈希、Mod 版本。

## 数据结构

| 结构 | 作用 |
| --- | --- |
| `RocketProfile` | 顶层档案：来源、环境、级段、部件、载荷、整流罩、参数、校准/运力占位 |
| `EnvironmentProfile` | KSP 根目录、游戏/craft 版本、ConfigCache/MechJeb/Validation DLL 哈希、Mod 版本、快照哈希、指纹 |
| `StageProfile` | 湿/干/推进剂质量、推力、Isp、点火限制、发动机组、油箱、部件、来源/置信度 |
| `PartProfile` | 部件质量、级段归属、`payload`/`fairing`/`stage`/`unknown` 角色、来源 |
| `ParameterEntry` | 参数键/值/单位、来源、来源哈希、不确定度、识别来源、置信度 |
| `TrialSpec` / `TrialResult` | 后续 campaign 的试验输入/输出契约 |
| `CapacityPoint` | 后续任意轨道的估算/验证/失败边界与置信区间 |
| `Encyclopedia` | profile + 运力点 + 试验 + 缺口 |

## 参数目录

每个参数都带 `source`、`source_sha256`、`confidence`：

- 运行时编辑器快照：`exact`，`identified_by=editor_snapshot`；
- craft/GameData 推导：`estimated` 或 `exact`（哈希/版本）；
- 未知：`unknown`，不静默填 0。

当前已覆盖：

- 级段湿/干质量、推进剂、推力、真空/海平面 Isp、点火限制；
- 发动机 cfg/数量、油箱字段（类型/容积等）、部件干/资源/湿质量；
- 环境哈希、游戏/craft 版本、Mod 版本。

## 已知边界

- **级段质量是编辑器分组质量**，可能包含载荷和整流罩；必须结合 `payload`/`fairings` 和 `payload_included`/`fairing_included` 使用，不能直接把 `wet_mass_t` 当作净火箭质量。
- 载荷需要显式声明（名称或后续 UI 标记）；不声明时为 `unknown`，不会自动猜。
- 并联/复杂拓扑、未知 Mod 行为仍标记待人工确认；不生成假参数。
- 尚未接入 T0/T1 仿真、校准、模型偏差和容量；`calibration/capacity/trials` 为空。
- `mass_reconciliation` 可能因模板载荷/Mod 动态质量出现残差，保留原始对账结果供复核。

## 下一步

Phase B：T0/T1 仿真内核 + 通用导入/百科；Phase C：campaign v1 + 遥测升级 + 220 km 参数标定。
