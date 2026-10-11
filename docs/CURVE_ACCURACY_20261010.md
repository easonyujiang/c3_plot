# 运力曲线精度推进记录（2026-10-10）

目前已能用一条命令，直接从指定游戏安装的最终发动机配置生成离线 C3–载荷曲线。数学计算中的明确错误已修正，但尚未用新的独立飞行测量验证物理精度。

## 已交付

- `plot_capacity.py`：独立绘图，不依赖数据库初始化；生成离线 HTML、CSV、JSON 和 SHA-256 来源指纹。
- `output/curve_accuracy_20261010/neutron_i_2630/`：当前火箭曲线、SVG/PNG 与浏览器检查记录。
- `installed_engine_audit.py`：读取实际安装的 ModuleManager.ConfigCache；发动机映射歧义、多模式或配置切换会中止，不静默回退到旧本地参数。
- `tools/fetch_mod_sources.py`：保存 SSTU、RealFuels、SolverEngines、MechJeb 的指定版本源码与提交指纹，清单在 `output/curve_accuracy_20261010/sources/manifest.json`。MechJeb 的源码标签 2.15.3.1 与安装记录 2.15.3 并非严格同一版本，不能宣称二进制完全对应。
- `game_plugin/C3MassTelemetry.cs`：被动采样插件，已经编译并安装到游戏 `GameData/C3MassTelemetry/Plugins`；不控制油门、分级或改写飞船与存档。加载后记录实际质量、发动机、资源、轨道速度和能量，并每 10 秒或分级时保存部件快照。
- `tools/analyze_telemetry.py`：独立复核快照部件质量之和、游戏总质量以及 C3 能量计算；不给轨道滑行片段自动赋予校准资格。

## 已修正的结果错误

1. 逆行发射损失原来只进入总需求，没有在详细燃料回放中消耗；现已计入上升段。220 km、19.81° 到逆行 GEO 的模型运力从约 0.502 t 修正为约 0.134 t。
2. 校准点的载荷支持范围与人为生成的曲线范围分开。当前三点为 1.055–4 t；范围外的任务模型结果不能自动成为推荐。
3. 请求 400 km 停泊轨道时，不再把固定 220 km 校准直接冒充覆盖，返回数据不足。
4. 并联分级的第一虚拟阶段原来重复携带剩余芯级质量；现使用独立解析火箭方程验证，并拒绝芯级先耗尽的未支持情况。
5. C3 超过零载荷极限返回零运力，曲线补上零载荷端点；C3=0 的筛选条件正常生效。
6. 已被火箭引用的发动机重载使用更新而非删除替换，避免外键错误。

## 实际游戏证据与限制

安装是 KSP 1.12.5、RSS/FAR/SMURFF/RealFuels/SSTU 等模组组合；不是可以直接套用完整 RO 假设的环境。现有存档记录还含 Principia，后续飞行必须记录其实际运行状态，不能把二体理想 C3 曲线当成完整多体轨迹验证。

最终配置中 Merlin-1B 为 400 kN、305 s 真空比冲；LR81 为 71 kN、300 s 真空比冲。SSTU 源码显示发动机簇会在运行时按布局缩放推力；实际运行时已经缩放的数值不能再乘布局数量。

反汇编安装中的 Assembly-CSharp.dll，确认已加载飞船 `Vessel.GetTotalMass()` 累加 `Part.mass + Part.GetResourceMass()`，因此采样采用游戏最终质量，避免重新猜测各模块的动态质量贡献。反汇编证据在 `runtime-mass-methods.json`。

找到了 `saves/RocketMaking/Ships/VAB/Neutrons I 2630.craft`，描述为 `4t 200km@20°`。其中 LR81 布局为 `Triple`、Merlin-1B 为 `SixCenter`，与 YAML 的 3 台及 6 台数量对应。这是身份与校准来源线索，不是新的独立飞行测量；现有质量、数量、三点校准仍须运行时确认。

三点二次拟合没有独立自由度，不能给出可信精度或统计误差条；有限推力、FAR 阻力、飞行策略、点火限制与级寿命均可能使实际可用运力低于理想推算。

## 数据安全与验证

接入修正曲线前，完整备份至 `output/curve_accuracy_20261010/original_data_backup`。正式 SQLite 与 YAML 已更新衍生计算结果；质量、发动机分组、校准配置未重新估计。更新后审计显示 SQLite 完整、外键正常、SQL/YAML 配置 matching；记录在 `applied-curve.json`。

已有核心修复通过 914 项测试；新增采样审计另有独立损坏数据测试。最终整套结果见 `tests-final-verified.txt`。旧 YAML 兼容性测试原先读可变的正式配置，现改为固定的带 NumPy 标签样本，避免正常更新计算结果使该测试失效。离线图表和真实主界面通过浏览器检查，390/768/1280 宽度下主界面无整体溢出、无脚本错误。

启动游戏检查时，日志确认采样 DLL 被加载和列入程序集清单，进入主菜单后输出 `[C3MassTelemetry] schema=2 loaded; passive flight observer ready`（第 133774 行），因此主菜单加载验收通过。随后日志持续出现 Unity 分配错误，另有前期 Steam 初始化错误；该检查进程已结束，没有载入存档，没有飞行样本。主菜单加载不等于 Flight 采样已验证，也不能仅凭这些日志确认其他启动问题的根因。完整日志保存在 `ksp-runtime-load.log`，有界扫描摘要在 `ksp-runtime-load-summary.json`。

## 下一阶段验收

续轮已取得 58 条真实在轨飞船样本并完成采样链路验收，详见 [游戏内验收记录](GAME_VALIDATION_20261010.md)。该样本属于另一套上面级，未加入当前曲线校准；以下运力独立验证仍待完成。

先解决当前启动检查环境，再在独立测试存档中确认飞船身份、发射台湿质量、发动机簇实际参数及分级干质量。使用相同发射场、轨道和飞行策略，补测至少一个未参与拟合的载荷点；记录最终轨道和剩余资源，明确可用剩余 Δv。只有独立点误差可以用于评价曲线精度。

采样日志输出到游戏 `GameData/C3MassTelemetry/Logs`。检查单次会话：

```powershell
.\.venv\Scripts\python.exe tools/analyze_telemetry.py "D:\SteamLibrary\steamapps\common\Kerbal Space Program\GameData\C3MassTelemetry\Logs\实际会话.csv" --output output/flight_audit.json
```

成功只说明日志中的质量和能量相互一致，不代表载荷曲线已经通过飞行验证。此阶段目标继续保持进行中。
