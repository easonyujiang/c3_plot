# 清零与游戏源测试

本次清除工作区中的 `VAB/`、`engines/`、`experiments/`、`rockets/` 的旧数据、`output/`、`backups/`、`docs/evidence/`、旧 SQLite 数据库及其 WAL/SHM 文件，以及 `.craft` / YAML 测试样例。不创建副本备份。Git 历史不在本次改写范围内。

真实游戏目录通过 `.env` 的 `C3_MASS_KSP_ROOT` 显式配置。本机目录为 `D:\SteamLibrary\steamapps\common\Kerbal Space Program`，WSL 为 `/mnt/d/SteamLibrary/steamapps/common/Kerbal Space Program`。

- 保存的火箭：`saves/*/Ships/VAB/*.craft`，按需读取，不扫描其他磁盘。
- 发动机与部件：游戏 `GameData`，不再从项目 `engines/` 回退。同名发动机文件有歧义时拒绝任意选取，使用游戏导入的运行时证据。
- 首次启动：仅数据库结构，所有业务表为空；不自动导入 YAML、发动机、标准任务或实验。
- 测试：临时数据库和输出自动清理；动态真实样本检查不依赖固定火箭名、固定数量、旧质量数值或历史校准。
- 算法代码、数据库迁移、依赖、前端构建、说明文档与规范表保留。代码内最小算法输入不是保存的游戏样本。
- 旧快照对应的固定数值回归已明确停用，不以伪造标定代替；新游戏输入也不能自动生成实测标定。

后续用户主动保存的火箭档案仍会进入数据库和 YAML 写穿存储，便于日常使用。项目不在启动时清除新建数据。
