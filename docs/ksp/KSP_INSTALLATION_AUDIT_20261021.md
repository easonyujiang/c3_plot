# 本机 KSP 安装与发射接口分析

核查日期：2026-10-21。来源为用户指定的 `D:\SteamLibrary\steamapps\common\Kerbal Space Program`，本轮只读检查，未改动游戏安装、模组或存档，未启动游戏。日志是 2026-10-11 的历史运行记录，不能替代一次新的目标存档实机检查。

## 游戏与关键环境

`buildID64.txt` 为 build 03190，`KSP.log` 头部为 KSP **1.12.5.3190**。Unity 可执行文件的 FileVersion 为 Unity 版本，不用于识别 KSP 版本。

| 组件 | 本轮证据 | 对运力/发射计算的影响与处理 |
| --- | --- | --- |
| RealSolarSystem | CKAN v20.1.3.0，存在最终配置缓存 | 地球尺寸、引力、大气与天体体系；需从实际环境导出，替换固定常数假设 |
| Kopernicus、ModularFlightIntegrator | 安装目录与 CKAN 记录 | 天体与飞行物理集成依赖，进入环境指纹 |
| KSCSwitcher | CKAN v2.2.0.0 | 发射场纬度、位置与可达轨道面影响运力；不能假定固定场址 |
| FAR Continued | CKAN 0.16.1.2，DLL FileVersion 0.16.1.2 | 气动阻力、姿态稳定、迎角与结构约束影响上升轨迹；存在本机 `Custom_FARAeroData.cfg` |
| RealFuels + RFStockalike | CKAN v15.14.0.0；RealFuels DLL 同为 15.14.0.0 | 燃料、发动机配置、节流、点火与沉底约束；具体是否启用还需读取设置和构型 |
| SolverEngines | CKAN v3.14.0；实际 DLL FileVersion **3.13.1.0** | 登记版本与程序集文件版本不同，可能来自上游版本标记，也可能有安装差异；按哈希核对源码后再作语义结论 |
| SMURFF | CKAN 1.9.1；最终缓存 tanklever/enginelever/podlever 均为 1 | 修改质量和性能；与其他补丁共同作用，不能重复施加理论修正 |
| SSTU | `.version` 为 0.12.50.162 | 发动机集群、模块化部件和动态质量，需编辑器/运行时快照 |
| ProceduralParts / ProceduralFairings | CKAN 2.8.0.0 / 6.7.0.0 | 可变尺寸、油箱、整流罩质量及抛弃事件影响构型和性能 |
| B9PartSwitch | CKAN 2.21.0.4 | 子型和资源切换影响质量与燃料；读取实际选择 |
| CryoEngines / CryoTanks / RealHeat | CKAN 2.0.8 / 1.6.7 / 5.1 | 相关发动机、热环境及蒸发/冷却设置需按实际模块验证，尤其长滑行任务 |
| RealChute | CKAN 1.4.9.5 | 降落伞等部件动态质量继续遵守已有证据授权范围 |
| Principia | 原生库与托管适配器存在；常用存档包含 Principia scenario | 多体轨道、参考系与有限燃烧任务规划；本轮未证明目标存档当前原生插件成功初始化 |
| MechJeb | `MechJeb2.dll` FileVersion **2.14.2.0** | 当前主要发射执行器，以此版本验证 PVG 参数与启停接口 |
| kOS | `kOS.dll` FileVersion **1.6.0.1** | 可选程序执行器/事件编排器，产品版本含 commit `d4388439c712994e47258ed7514e863f79da632b` |

未发现 `RealismOverhaul` 或 `RP-0` 的同名 GameData 目录；不能因使用 RSS 和 RealFuels 就按完整 RO/RP-1 默认参数计算。其他部件包也会影响所使用构型，最终仍以完整 ModuleManager 缓存、选中部件与运行时快照为准。

视觉模组通常不是第一批运力建模对象，但实际物理步长和加载环境仍应进入试验记录。AtmosphereAutopilot、MechJeb、kOS 等控制器的使用状态需显式记录，避免多个程序同时接管同一控制通道。

## MechJeb 的实际版本和配置

当前插件目录中同时存在：

- `MechJeb2.dll`：FileVersion 2.14.2.0，SHA-256 `277a285d4212ab594e157f03b46de408b283e03cab0518c4ec312c4caf91253b`。
- `MechJeb2.dll.new`：FileVersion 2.16.0.0，SHA-256 `b844c23311a0ca9e51f4856a5af6ddd731340a8cc6b193c96d03ef37e31616d8`。
- 另有 `MechJebLib.dll.bak`。

`.new` 和 `.bak` 不作为当前已加载版本的证据；旧日志也仅表明当次加载了正常 DLL 路径。本轮没有替换这些文件。后续若升级到 2.16，需要单独的兼容验证和新环境指纹。

只读查看 `Plugins/PluginData/MechJeb2/mechjeb_settings_global.cfg` 得到以下保存值：

| 参数 | 保存值 | 解释 |
| --- | --- | --- |
| `ascentPathIdx` | 2 | 对应 2.14.2 源码枚举中的 PVG |
| `desiredOrbitAltitude` / `DesiredApoapsis` | 220000 m / 220000 m | 目标高度相关字段，需运行时读回确认 |
| `desiredInclination` | 约 24.42585° | 保存的目标倾角，不作为后续任务默认值 |
| `PitchStartVelocity` | 50 m/s | 俯仰起始速度 |
| `PitchRate` | 0.5°/s | 俯仰速率 |
| `FixedCoast` / `FixedCoastLength` | true / 0 s | 当前保存的滑行配置 |
| `DynamicPressureTrigger` | 10000 Pa | PVG 接入相关条件 |
| `limitDynamicPressure` / `maxDynamicPressure` | true / 38000 Pa | 当前动压限制 |
| `limitAcceleration` / `maxAcceleration` | true / 50 m/s² | 当前加速度限制 |
| 自动分级 / `skipCircularization` | true / true | 必须与具体任务和上面级点火能力一起核对 |

这些值来自全局配置，可能被船只配置或运行时状态覆盖，尚不能声称每枚火箭都按这套参数发射，也不是推荐最优参数。

## 对应源码核查

通过官方版本文件提交历史定位到 [MechJeb 2.14.2 提交 `6f1f2a34`](https://github.com/MuMech/MechJeb2/commit/6f1f2a34ae035073d22185f53e23334ee59845d7)，下载并检查 5 个文件，保存在本地忽略目录 `output/strategy_20261021/sources/`。对应 AssemblyInfo 的 FileVersion 为 2.14.2.0，与本机正常 DLL 的文件版本一致；尚未证明整个 DLL 是该提交的可复现构建。

- [AscentAutopilot](https://github.com/MuMech/MechJeb2/blob/6f1f2a34ae035073d22185f53e23334ee59845d7/MechJeb2/MechJebModuleAscentAutopilot.cs)：枚举为 Classic/GravityTurn/PVG；`ascentPathIdxPublic` 属性包含模块切换逻辑，启停涉及态控、推力和分级的 users 管理。
- [AscentPVG](https://github.com/MuMech/MechJeb2/blob/6f1f2a34ae035073d22185f53e23334ee59845d7/MechJeb2/MechJebModuleAscentPVG.cs)：存在俯仰、滑行、目标轨道及接入条件配置，提供参数搜索入口；源码内部按目标与阶段建立制导约束。
- [AscentGuidance](https://github.com/MuMech/MechJeb2/blob/6f1f2a34ae035073d22185f53e23334ee59845d7/MechJeb2/MechJebModuleAscentGuidance.cs) 与 [ThrustController](https://github.com/MuMech/MechJeb2/blob/6f1f2a34ae035073d22185f53e23334ee59845d7/MechJeb2/MechJebModuleThrustController.cs)：UI/推力限制需要与 PVG 执行路径一起核查，不能照抄其他版本的字段说明。

项目已有源码归档包含 MechJeb 2.15.3.1 与 SolverEngines v3.14.0。它们不自动证明本机当前 DLL 的行为；本轮因此单独取得 MechJeb 2.14.2 源码。已有 RealFuels/SSTU 源码可辅助理解质量和点火，仍需与实际构型、设置和哈希相连。

本轮尝试使用现有窄域 IL 读取器检查 MechJeb/kOS 时，遇到它未支持的 ManifestResource 元数据表，未得到完整分析；没有据此生成程序集方法结论。接口开发阶段可扩展读取器或在独立工具目录使用 ILSpy/Mono.Cecil，先读取目标类型/方法，再按需反编译；原生 Principia 另按其官方源码与运行日志核查。本轮无需下载完整游戏或向仓库提交游戏 DLL。

## 现有项目能复用什么

- `server/ksp_install_index.py`、编辑器快照与质量证据链：读取最终配置、匹配构型和运行时质量。
- `c3_mass/`：快速运力曲线、燃烧序列、级段与载荷搜索，用于候选筛选。
- `game_plugin/C3MassTelemetry.cs`：已有被动飞行遥测，约 0.5 s 采样，记录质量、轨道、推力和分级；需要补动压、迎角、加速度、姿态、实际发动机状态和 MechJeb 制导状态。
- `tools/game/prepare_game_validation.py`、`tools/game/run_game_replay.py`：已有隔离存档与受限运行基础，但尚不等于自动发射、试验复位和载荷优化队列。

本轮读取到两个常用候选存档 VAB 中分别有 37 和 21 个 craft，另有试验存档。尚未去重、导入和逐一验证，不能按 craft 文件数量宣称已经拥有同样数量的有效火箭型号。

安装文件哈希与筛选后的 CKAN 元数据见 [证据清单](../evidence/ksp_installation_20261021.json)。原始源码、日志和下载尝试保留在本地 output，不随项目源码上传。后续执行顺序见 [产品路线图](../PRODUCT_ROADMAP.md)。
