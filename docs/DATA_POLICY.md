# 数据策略：清零、游戏源与冻结证据

更新：2026-10-24。本文描述当前工作树的数据边界；2026-10-21 的一次性清理记录保留在文末，供追溯。

## 当前规则

- **业务数据是本地数据。** `c3_plot.db`、`rockets/`、`output/`、`backups/` 由用户操作产生，不随 Git 提交。
- **游戏输入不在项目内复制。** 火箭从 `saves/*/Ships/VAB/*.craft` 按需读取，部件与发动机从 `GameData` 读取；路径由 `.env` 的 `C3_MASS_KSP_ROOT` 指定。游戏目录只读。
- **首次启动只建结构。** 不自动导入 YAML、发动机、标准任务、火箭样例或历史实验结果；所有业务表为空。
- **冻结证据是显式提交的例外。** 只有经过审计、带 SHA-256 清单的飞行证据进入 `docs/evidence/<folder>/`。证据包不是业务样例，不参与启动导入，也不能替代新的试飞。
- **证据包保持原始字节。** `.gitattributes` 对 `docs/evidence/**` 使用 `-text -diff`，避免换行归一化改变已记录的哈希。
- **测试使用隔离数据。** 临时数据库、临时输出和临时 YAML 在测试结束后移除；真实游戏数据只做只读动态检查。
- **旧数据不恢复为固定样例。** 2026-10-21 清除的 VAB、发动机、实验、火箭 YAML、旧 SQLite 和输出仍在 Git 历史中可查，但不再作为当前结果或回归基线。

## 当前证据

- [C3Baseline20261024F 基线飞行](evidence/C3Baseline20261024F/README.md)：1.000 t 到 220 × 220 km、20.00008°，保持 30.62 s。这是一次交付点，不是最大运力。
- [C3Baseline20261024G 基线飞行](evidence/C3Baseline20261024G/README.md)：2.500 t 到 220 × 220 km、20.00010°，保持 30.60 s。这是第二个交付点，仍不是最大运力。
- [KSP 安装与模组审计](evidence/ksp_installation_20261021.json)：2026-10-21 的安装文件与 CKAN 元数据快照。
- 现场原始遥测仍保存在 KSP 的 `PluginData/C3Baseline/`；仓库中的证据包是经过复制和哈希的冻结副本。

## 2026-10-21 清零记录（历史）

本次清除工作区中的 `VAB/`、`engines/`、`experiments/`、`rockets/` 的旧数据、`output/`、`backups/`、旧 SQLite 数据库及其 WAL/SHM 文件，以及 `.craft` / YAML 测试样例。不创建副本备份。Git 历史不在本次改写范围内。

真实游戏目录通过 `.env` 的 `C3_MASS_KSP_ROOT` 显式配置。本机目录为 `D:\SteamLibrary\steamapps\common\Kerbal Space Program`，WSL 为 `/mnt/d/SteamLibrary/steamapps/common/Kerbal Space Program`。

- 保存的火箭：`saves/*/Ships/VAB/*.craft`，按需读取，不扫描其他磁盘。
- 发动机与部件：游戏 `GameData`，不再从项目 `engines/` 回退。同名发动机文件有歧义时拒绝任意选取，使用游戏导入的运行时证据。
- 测试：临时数据库和输出自动清理；动态真实样本检查不依赖固定火箭名、固定数量、旧质量数值或历史校准。
- 旧快照对应的固定数值回归已明确停用，不以伪造标定代替；新游戏输入也不能自动生成实测标定。

后续用户主动保存的火箭档案仍会进入数据库和 YAML 写穿存储，便于日常使用。项目不在启动时清除新建数据。
