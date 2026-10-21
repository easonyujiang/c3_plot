# 目录整理与维护

2026-10-21 完成本轮整理。旧实现可从 Git 历史和 `v0.1.0` 标签查看，当前工作树不另设旧代码归档。

## 已退役

- 整个 `dashboard/` 及其 `/legacy/`、`/css/`、`/js/` 服务入口；React 中的旧界面链接与样式同步移除。
- 只服务旧界面的 DOM、模板注入与脚本测试。保留仍有意义的检查，重建为当前 React 工作台的资源路由/编码测试和统一浏览器交互检查。
- 多份旧 UI 浏览器脚本，包括失效选择器、固定试验存档/质量和重复的日期目录。当前检查集中于 `frontend/tests/workspace.cjs`。
- 一次性旧曲线发布/修复工具、固定日期的工作区清理和游戏启动修复脚本。
- 旧阶段计划、最终路线、项目复盘及重复工程清单。当前产品方向统一由 `PRODUCT_ROADMAP.md` 描述。
- 四份推进/验收过程文档，仍有效的测量、运行参数与精度限制合并到 `validation/BASELINE.md`。
- 重复依赖入口和未使用的 pytest 开发依赖。运行依赖版本合并到 `requirements.txt`，后端测试使用 unittest。
- 未再使用的 Python 包装命令和启动器中的本机 Anaconda 路径。

## 当前目录职责

`c3_mass/` 存放计算模型；`server/` 存放 API、持久化和游戏配置读取。实际安装发动机解析归入 `server/installed_engine_audit.py`。

工具通过 `python -m` 调用：`tools.data`、`tools.plot_capacity` 是日常入口，`tools/game/` 是游戏与遥测工具，`tools/research/` 是质量和源码研究，`tools/release/` 是发布，`tools/dev/` 是开发验证服务。共享路径位于 `tools/paths.py`。

插件 C# 源码集中于 `game_plugin/`，构建和只读 API 检查入口位于 `tools/game/`。插件输出为 `output/plugins/`，发布包为 `output/releases/<版本>/`，不再绑定某次验收日期。

文档按 `models/`、`ksp/`、`validation/`、`evidence/` 分类。根目录只保留应用启动、测试、项目说明与必要配置。

## 必须保留的历史数据

以下内容仍是当前运行、兼容性或证据链的一部分，不能因名字中含 legacy、旧日期或旧版本就删除：

- `c3_plot.db`、`rockets/` 和本地备份。
- `engines/`：首启发动机库、样例计算和测试仍读取这些配置。
- `VAB/` 及其 original/loadmeta：来源核对和构型回归输入。
- `experiments/control_mass/`：生产质量证据门控及真实样本回归仍依赖原文件和哈希。
- 全部 SQL 迁移：旧数据库升级需要按序执行。
- YAML 读取兼容和数据库只读兼容：现有用户数据仍可能使用旧格式。
- 本地唯一的游戏遥测、原始实验、源文件指纹和数据修改前备份。

本轮对数据库、rockets、engines、VAB、experiments 和 backups 保存清理前后的逐文件 SHA-256 核对。清理缓存和可再生成的旧构建/截图时不改动这些内容。

## 验证方式

从根目录运行 `python run_tests.py`；它隔离数据库、YAML 与输出，单次测试遗留的临时文件也在该轮临时根目录中清理。

前端执行 `npm ci`、`npm run build`，然后针对隔离服务运行 `npm run check:ui`。发布验证需解压运行包、核对清单，并用全新数据库访问首页、资源和任务 API。详见 [工具入口](../tools/README.md)。

正式发布仍要求干净工作区与匹配版本标签，本轮不移动原有标签。目录迁移不代表获得新的物理精度或完成新任务实飞。

## 本轮检查结果

- 完整隔离回归：925 项通过，零失败、零错误、零跳过。
- TypeScript 检查与 Vite 生产构建通过，交付 dist 已更新。
- Edge 实际浏览器在 390/768/1440 像素通过悬停、滚轮/框选缩放、平移、恢复、整流罩切换、取消编辑不写入及任务查询，无页面错误与横向溢出。
- 17 个 Python 模块命令的 `--help` 均正常。
- 按新运行清单制作验证包，核对 179 个文件哈希；解压后使用全新数据库通过首页、资源和 700 kg 任务 API 检查。这是工作树验证包，不是新标签发布。
- 项目导入路径、文档本地链接和 Git 空白检查通过。

本地机器可读结果位于 `output/maintenance_20261021/` 与 `output/browser-checks/`。本轮没有安装或修改游戏插件，也没有执行新的物理运力试飞。
