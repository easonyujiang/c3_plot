# 工具入口

在项目根目录使用 `python -m tools.<模块>`，不直接运行嵌套 Python 文件。工具共享 `tools.paths.ROOT` 解析项目路径，目录搬迁不需要修改代码。

| 用途 | 命令 |
| --- | --- |
| 只读审计、备份、恢复 | `python -m tools.data --help` |
| 从实际游戏配置绘图 | `python -m tools.plot_capacity --help` |
| 临时数据库的界面验证服务 | `python -m tools.dev.serve_validation --help` |
| 飞行遥测审计 | `python -m tools.game.analyze_telemetry --help` |
| 发射台快照比较 | `python -m tools.game.compare_pad_snapshot --help` |
| 存档快照盘点 | `python -m tools.game.inventory_flight_saves --help` |
| 启动日志分析 | `python -m tools.game.inspect_game_startup --help` |
| 准备隔离游戏回放 | `python -m tools.game.prepare_game_validation --help` |
| 执行已准备的隔离回放 | `python -m tools.game.run_game_replay --help` |
| 准备隔离基线试飞 | `python -m tools.game.prepare_baseline --help` |
| 只读核对基线试飞 | `python -m tools.game.audit_baseline --help` |
| 固化基线证据包 | `python -m tools.game.freeze_baseline_evidence --help` |
| 质量残差诊断 | `python -m tools.research.audit_ksp_mass_residuals --help` |
| 受控质量实验 | `python -m tools.research.control_mass_experiment --help` |
| 游戏质量方法 IL 检查 | `python -m tools.research.audit_editor_mass --help` |
| 模组性能/制导源码获取 | `python -m tools.research.fetch_mod_sources --help` |
| 动态质量源码与补丁获取 | `python -m tools.research.fetch_mass_sources --help` |
| Neutron 样例独立公式复算 | `python -m tools.research.verify_neutron_independent --help` |
| 标记版本发布打包 | `python -m tools.release.package_release --help` |
| 发布包独立解压验证 | `python -m tools.release.smoke_release --help` |

游戏插件的 Windows 构建入口：

```powershell
powershell -File tools/game/build_telemetry.ps1 -KspRoot "D:\SteamLibrary\steamapps\common\Kerbal Space Program"
powershell -File tools/game/build_validation.ps1 -KspRoot "D:\SteamLibrary\steamapps\common\Kerbal Space Program"
```

产物写入 `output/plugins/`。默认只构建，`-Install` 才会安装；替换已有文件需要其预期哈希。`InspectKspApi.cs` 和 `InspectEditorApi.cs` 是只读程序集检查源码。验证插件只用于显式准备的独立回放或隔离基线试飞，不属于正式发布包。基线证据包写入 `docs/evidence/<folder>/`，由 `freeze_baseline_evidence` 只读复制并哈希。

浏览器检查统一在 [frontend/tests/workspace.cjs](../frontend/tests/workspace.cjs)，执行说明见 [前端说明](../frontend/README.md)。

发布要求 Git 工作区干净，`v<VERSION>` 标签指向 HEAD。源码包保留完整项目，运行包使用显式文件清单；包含工具的运行时依赖，不带测试、研究脚本、旧界面或内部回放插件。输出为 `output/releases/<版本>/`。`v0.2.0` 标签已创建，原有 `v0.1.0` 标签未移动。

研究脚本的版本/样例范围需要按实际安装核对：固定提交不等于证明 DLL 为对应源码的可复现构建。独立公式复算验证算术一致性，不能证明火箭实际运力。

全部维护工具随源码提供；精简运行包只包含数据、绘图与遥测插件构建所需工具。后端测试和浏览器检查应在源码检出中运行。
