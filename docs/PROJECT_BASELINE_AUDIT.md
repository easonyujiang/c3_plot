# C3-Mass 项目基线审查（阶段 1：建立可信基线）

- 审查日期：2026-07-31
- 审查范围：只读。未修改任何业务代码、数据库或用户数据。
- 审查方式：源码阅读 + 只读 SQLite 查询 + 运行现有测试（9/9 通过）。
- 数据库文件 `c3_plot.db` 在审查前后均未写入（未执行任何 INSERT/UPDATE/DELETE/ALTER；本次审查只运行了 `SELECT` / `PRAGMA`）。

---

## 1. 当前架构图 / 模块清单

```
┌─────────────────────────── 浏览器前端 (dashboard/) ───────────────────────────┐
│ index.html · css/ · js/ (api.js · editor.js · import.js · tree.js · …)         │
│   编辑器保存: editor.js save() → Api.Rockets.create/update → POST/PUT /api/rockets │
│   批量导入:   import.js handleFiles/confirmBatch → /api/import/batch/preview|confirm │
│   VAB 单文件: import.js previewVab → /api/import/vab/preview → YAML 文本框 → paste │
└──────────────────────────────┬────────────────────────────────────────────────┘
                               │ HTTP/JSON
┌──────────────────────────────▼─────── Flask 服务端 (server/) ──────────────────┐
│ __init__.py  create_app(): init_db + 9 个蓝图注册                                │
│ routes_rockets.py 火箭 CRUD      routes_import.py  YAML/VAB/批量导入            │
│ routes_data.py    data/recompute/orbit-calc/mission-query/find                 │
│ routes_missions.py 标准任务       routes_export.py CSV/JSON 导出(仅计算数据)      │
│ routes_query.py  高级筛选/统计      routes_compare.py 多火箭对比                  │
│ routes_folders.py 文件夹          routes_engines.py 发动机列表/reload            │
│ import_service.py 统一预览-确认批量管线   vab_parser.py .craft 解析               │
│ validation.py  配置校验          yaml_codec.py 安全 YAML 读取(LenientSafeLoader) │
│ models.py 数据访问层(所有 SQL + YAML 备份写入)   database.py 初始化/自动迁移/引擎导入 │
└──────────────────────────────┬────────────────────────────────────────────────┘
                               │ sqlite3（无 ORM，raw SQL + Row 工厂）
┌──────────────────────────────▼─────────────────────────────────────────────────┐
│ SQLite  c3_plot.db (14 表, WAL)    │ 文件系统:  rockets/*.yaml(备份)            │
│ rockets/stages/stage_engines/      │  engines/*.cfg(解析源)  output/<slug>/data.json │
│ calibration_points/reference_missions/computation_results/                      │
│ c3_mass_curve_points/dv_curve_points/standard_missions/import_sources/          │
│ folders/activity_log                                                            │
└────────────────────────────────────────────────────────────────────────────────┘
顶层:  compute_c3_mass.py(纯计算引擎: compute_rocket / compute_orbit_mission) · engine_parser.py(.cfg解析) · run.py(入口)
```

### 核心模块职责与位置

| 模块 | 职责 | 关键位置 |
|---|---|---|
| `compute_c3_mass.py` | 纯计算：C3-Mass 曲线、dv 曲线、轨道任务预算 | `compute_rocket` L93、`compute_orbit_mission` L329、`compute_orbit_c3` L497 |
| `engine_parser.py` | 解析 `engines/*.cfg` 的推力/Isp | `extract_engine_params` L52 |
| `server/models.py` | 数据访问层，全部 CRUD；YAML 备份的原子写 | `create_rocket` L277、`update_rocket` L299、`save_computed_data` L420、`find_rockets_for_orbit` L619 |
| `server/database.py` | 启动初始化、幂等迁移、引擎导入、YAML 首启自动迁移 | `init_db` L124、`_auto_migrate` L171、`_repair_missing_stage_engines` L54 |
| `server/vab_parser.py` | .craft → 可编辑草稿 | `parse_craft` L51 |
| `server/import_service.py` | 批量"预览—确认"管线、SHA-256 去重、.loadmeta 配对 | `preview_batch` L112、`confirm_batch` L136 |
| `server/validation.py` | 火箭配置校验（级段/引擎/校准/任务） | `validate_rocket` L28 |
| `server/routes_*.py` | HTTP 端点（9 个蓝图） | 见上表 |
| `dashboard/js/` | 前端（编辑器、导入、查询） | `editor.js` L254 save()、`import.js` L104 draftToYaml |

### 数据库现状（只读检查，2026-07-31）

- `c3_plot.db`：14 张表；**`PRAGMA user_version = 0`**（无迁移版本跟踪）。
- 火箭 1 枚：`Neutron I 2630`（slug `neutron_i_2630`，无文件夹）。活动日志显示其由"Migrated from YAML"（auto-migrate）创建。
- 发动机 74 条（`engines` 表），标准任务 12 条（含唯一索引 `idx_standard_missions_unique(label,c3)`）。
- `import_sources` 表为空（该火箭并非通过批量导入产生）。
- `stages` 两行：`stage_index 0` = SC-ENG-Merlin-1B ×6（multi_ignition=0），`stage_index 1` = SC-ENG-LR81-8096 ×3（multi_ignition=1）。

---

## 2. 已确认的数据流

### 2.1 火箭级序与 stage_index 定义（问题 1 的结论）

**结论：`stages` 列表 / `stages.stage_index` 从 0 开始递增，`0` = 最先点火的第一级（堆栈最底部），最大索引 = 末级（最后燃烧的上面级）。**

证据链（注意：`total_vac_dv` 的 `reversed(stages)` 只是从上面级向下累积"上方质量"的记账手段，见 `compute_c3_mass.py:39-48`，**不能单独作为实际点火顺序的证明**）：

- **YAML 现有级段顺序**：`rockets/neutron_i_2630.yaml:52-66` 先列第一级（`SC-ENG-Merlin-1B ×6`，湿 132.03 t），后列末级（`SC-ENG-LR81-8096 ×3`，湿 19.467 t）。
- **编辑器显示与保存顺序**：`dashboard/js/editor.js:135-163` `addStage` 按 DOM 顺序标注"第 N 级"，`save()`（L260-275）按同一顺序收集级段；`models.py:851-855`（`_insert_related`）按列表顺序写入 `stage_index` 0..n-1。
- **`.craft` 的 KSP `istg` 降序转换**：`server/vab_parser.py:97` `sorted(grouped, reverse=True)` —— KSP 大编号先点火，转换为项目"先列先点火"顺序。**注意 KSP 的 stage 编号与项目 stage_index 方向相反**（KSP stage 0 = 末级），解析结果保留原始 `ksp_stage` 字段但项目内不依赖它。
- **`find_rockets_for_orbit` 现有契约**：`server/models.py:645-647` 用 `ORDER BY stage_index DESC LIMIT 1` 取最大索引作为末级判定重启能力。
- `server/models.py:218` `get_rocket_config` 按 `stage_index` 升序返回给编辑器（第 1 级 = stage_index 0）。
- `server/validation.py:90-91` 禁止 `parallel` 级出现在最后一位 —— 并联助推级必须紧跟其芯级（`compute_c3_mass.py:127-129` 中 parallel 级 i 与 i+1 级合成燃烧阶段）。
- 实例：与 DB `stages` 表一致（`stage_index 0` = Merlin-1B ×6，`stage_index 1` = LR81-8096 ×3 且 multi_ignition=1）。

### 2.2 YAML → SQL 路径

**注意：并非所有 YAML→SQL 路径都经过 `validate_rocket`，必须区分：**

- **Web 导入与编辑器保存**（上传 / 粘贴 / VAB 确认 / 批量确认 / POST·PUT `/api/rockets`）：经 `yaml_codec.load_config`（L19，LenientSafeLoader 丢弃 legacy numpy 标签）→ `validation.validate_rocket`（L28）→ `compute_c3_mass.compute_rocket`（L93）→ `models.create_rocket`/`update_rocket`（L277/L299）→ SQL 写入 + `_sync_yaml` 备份。
- **首启自动迁移 `_auto_migrate`（`database.py:171-224`）不调用 `validate_rocket`**：直接 `load_config` → 优先读 `output/<slug>/data.json` 缓存（L200-207）否则 `compute_rocket` → `_insert_rocket_from_config`（L226）写库，全程只依赖 try/except 跳过坏文件（L192-194 解析错误、L213-215 计算错误、L221-223 写库错误）。

入口有 4 个：

1. **首启自动迁移**：`database.py:151-158` `init_db` → `_auto_migrate`（L171，仅当 rockets 表为空）→ `_insert_rocket_from_config`（L226）。优先读取 `output/<slug>/data.json` 缓存结果（L200-207），否则现场计算。
2. **多文件 YAML 上传**：`POST /api/import/yaml`（`routes_import.py:23`）。
3. **YAML 粘贴**：`POST /api/import/paste`（`routes_import.py:83`）。
4. **批量导入**：`POST /api/import/batch/preview`（`routes_import.py:171`）→ `import_service.preview_batch`（L112，不写库）→ `POST /api/import/batch/confirm`（L185）→ `confirm_batch`（L136，逐项计算+写入，SHA-256 去重，同名更新需 `allow_updates`）。

### 2.3 SQL → YAML 路径

**只存在"写穿（write-through）"方向：SQL 是唯一事实来源，YAML 只是每次写入时的原子备份。**

- `models._sync_yaml`（L45-55）：临时文件 + `os.replace` 原子写，剔除 `_` 前缀内部字段。
- 触发点：`create_rocket`（L290）、`update_rocket`（L321）、`save_computed_data`（L466）；失败时 `_restore_yaml`（L89）回滚备份。
- **没有"SQL → YAML 导出"接口**：`routes_export.py` 只有 CSV（L13）与 JSON（L58）导出 *计算数据*（`computation_results.data_json`），不导出可编辑配置。
- 编辑器读取走 SQL：`GET /api/rockets/<slug>`（`routes_rockets.py:25`）→ `models.get_rocket_config`（L205）。

### 2.4 编辑器保存路径

`dashboard/js/editor.js:254-312` `save()`：收集级段（主引擎 `engines` + 保留的 `engine_groups`，L260-275）→ 构造 cfg → 前端校验（L218）→ `Api.Rockets.create/update`（`api.js:9-10`）→ `POST/PUT /api/rockets`（`routes_rockets.py:33/58`）→ `validate_rocket` → `compute_rocket` → `models.create_rocket/update_rocket`（SQL + YAML 同步）。重算：`POST /api/recompute/<slug>`（`routes_data.py:43`）→ `save_computed_data`（L420，SQL + YAML）。

### 2.5 compute_rocket / compute_orbit_mission / find_rockets_for_orbit 调用关系（问题 3 的结论）

```
compute_rocket(cfg)                                  ← 调用者：
  compute_c3_mass.py:93                                routes_rockets.py:46,70（创建/更新）
                                                        routes_import.py:53,110,158（YAML 上传/粘贴/VAB 确认）
                                                        routes_data.py:50（recompute）
                                                        import_service.py:160（批量确认）
                                                        database.py:183,212（首启自动迁移）

compute_orbit_mission(...)                           ← 调用者：
  compute_c3_mass.py:329                               routes_data.py:119（/api/mission-query）
    └─ compute_orbit_c3() L497（兼容包装）              routes_data.py:81（/api/orbit-calc）
         ↔ routes_data.py:69-82

models.find_rockets_for_orbit(required_dv, payload, required_post_parking_ignitions)
  models.py:619                                      ← 唯一调用者：
                                                        routes_data.py:129（/api/mission-query）
  内部：dv_curve_points 反插值 interpolate_payload_for_dv (L592)
        末级 multi_ignition 判断 supports_restart (L645-650)

models.find_rockets_for_mission(payload_t, c3)       ← 独立旧路径（C3 筛选，无重启概念）
  models.py:567                                        routes_data.py:162（/api/find）
```

**要点**：`compute_rocket` 与 `compute_orbit_mission` 之间**没有直接调用**；两者在 `/api/mission-query`（`routes_data.py:118-133`）中汇聚 —— 前者预计算并持久化每枚火箭的 dv 曲线，后者生成任务燃烧预算，`find_rockets_for_orbit` 用 `mission['required_dv']` 与 `post_parking_ignitions` 对每枚火箭排名。

### 2.6 multi_ignition 生命周期（问题 4 的结论）

| 环节 | 位置 | 说明 |
|---|---|---|
| 产生 | `server/vab_parser.py:115` | .craft 解析**恒为 False**（无重启推断） |
| 产生 | `dashboard/js/editor.js:159,272` | 编辑器复选框，默认 false |
| 产生 | YAML 文件 | 用户手写，如 `rockets/neutron_i_2630.yaml:57,64` |
| 读入计算 | `compute_c3_mass.py:120` | 带入 raw_stages['multi']；并行级展开只保留芯级 multi（L174），普通级保留（L183）；**但 `result['stages']`（L316-321）不输出 multi** → `computation_results.data_json` 中无该字段 |
| 保存 SQL | `models.py:848`、`database.py:242`、`database.py:93` | `stages.multi_ignition` INTEGER 0/1 |
| 读回 | `models.py:254` | `get_rocket_config` 转为 bool 给编辑器 |
| 使用 | `models.py:645-650` | `find_rockets_for_orbit`：仅取**末级**（stage_index 最大）布尔值 → `supports_restart`；任务 `post_parking_ignitions > 1` 时过滤掉不可重启火箭（仅标记，不推荐） |
| 使用 | `compute_c3_mass.py:120-183` | 仅透传，不参与任何 dv 计算 |

**结论**：multi_ignition 目前只被 `find_rockets_for_orbit` 作为末级的"一阶重启能力"布尔筛选使用，没有任何逐级/逐次点火建模。

---

## 3. 问题清单（按 P0 / P1 / P2 排序）

### P0 —— 影响数据正确性或直接阻断后续阶段

1. **SSTU 布局计数表缺少 `Double`**：`server/vab_parser.py:66` 的 fallback 为 `{Single:1, Twin:2, Triple:3, Quad:4, SixRadial:6, SixCenter:6}`，不含 `Double`。实测 `VAB/1T_Test.craft` 的 **stage 3（istg=3）** 中 `SSTU-SC-ENG-LR81-8096` 布局为 `Double`，被计为 **1 台**并仅产生警告（警告文本见 `vab_parser.py:69`），而正确台数为 2。现有测试 `test_vab_parser_preserves_multiple_engine_groups`（`tests/test_import_pipeline.py:17-22`）断言的是 **stage 6** 多发动机组总数（Merlin-1A ×3 + MassiveBooster ×3 = 6），**并没有覆盖 stage 3 的 `Double` 计数**。`docs/VAB_IMPORT_DESIGN.md:49-61` 明确要求从用户 KSP 安装读取布局计数（当前未实现，仍硬编码）。本阶段只记录，不修复解析器。
2. **备用 YAML 文本路径保真度不足（`draftToYaml` 丢弃 `engine_groups`）**：主编辑流程不受影响 —— `previewVab()`（`import.js:73-96`）触发 `vab-draft-ready`（L88-90），`app.js:46-49` 把解析出的**原始 draft 直接交给 `openEditorDraft`**（`editor.js:81-115`），`addStage` 通过 `dataset.engineGroups` 保留多发动机组（`editor.js:147-150`），批量导入路径同样保留（`import_service.py:69-89`）。真正的问题在备用副本：`draftToYaml`（`import.js:104-122`）把草稿渲染成 YAML 文本时只输出旧字段 `engines: {cfg, count}`，若用户从该文本走粘贴导入（`/api/import/paste`），`engine_groups` 会在文本副本中丢失，违反 `docs/BATCH_IMPORT.md:28-29` 的承诺。
3. **并联级（parallel）计算展开不可逆，逐级状态无法回放**：`compute_c3_mass.py:126-185` 把"助推+芯级"合成为一个虚拟阶段（phase1 为组合湿/干质量），再拆出芯级阶段；`computation_results.stages` 里的阶段数与 SQL `stages` 表**不一致**（展开后多一级、质量非原始值）。阶段 3 的逐级燃烧求解器不能直接使用 `result['stages']`，否则推进剂/质量回放错误；且展开时助推级的 `multi` 被丢弃（L174 只保留芯级）。
4. **`find_rockets_for_orbit` 只查询末级 multi_ignition，与并联级展开后的物理结构脱节**：`models.py:645-650` 用 SQL 原始级（展开前）的末级标志做筛选，而运力来自展开后的 dv 曲线 —— 一旦阶段 3/4 引入逐级验证，筛选逻辑必须整体替换，但现有字段必须保留（见 §5）。

### P1 —— 重要但不阻断基线

5. **无 SQL↔YAML 一致性检查与恢复路径**：YAML 仅写穿备份（§2.3），读取只发生在首启自动迁移与 `_repair_missing_stage_engines`（`database.py:54-107`，仅在 `stage_engines` 表全空时运行）。阶段 7 计划的一致性接口（仅 SQL / 仅 YAML / 内容不一致）当前不存在。
6. **YAML 往返会积累并丢失派生字段**：`save_computed_data`（`models.py:428-438`）把计算出的 `payload_t`/`dv` 合并进 YAML `reference_missions`，但 `get_rocket_config`（`models.py:266-272`）读回时只保留 `label/c3/apoapsis_km/color`，往返后派生字段消失；若某日实现"从 YAML 恢复"，数据会不一致。
7. **两个 slugify 实现不一致**：`database.py:197`（`re.sub(r'[^a-z0-9]+','_')`，纯 ASCII）与 `models.py:20-27`（Unicode `\w`）。自动迁移产出的 slug 可能与 `models.slugify()` 不同，导致更新/查重路径对不上（当前仅 1 枚火箭，恰好一致）。
8. **.loadmeta 仅作展示元数据**：`import_service.py:23-32` 只解析 `key = value` 与 `partNames`；`totalMass`/`partCount` 不参与质量对账（`docs/VAB_IMPORT_DESIGN.md:83-93` 设计有对账，未实现）。干质量固定为湿质量 ×0.18（`vab_parser.py:112`）。
9. **引擎导入只发生在表空时**：`database.py:146-149`，之后 `engines/` 新增 .cfg 需要手动 `POST /api/engines/reload`（`routes_engines.py:22`），否则新火箭引用新引擎时校验失败（`validation.py:81-83`）。

### P2 —— 改进项

10. **迁移无版本跟踪**：`PRAGMA user_version = 0`，`init_db`（`database.py:124-142`）每次启动重跑 001-005（幂等语句）。新迁移必须保持幂等；`004_dedupe_standard_missions.sql` 含 `DELETE`（启动时清理重复任务）值得注意。
11. **`output/neutron_i_2630/data.json` 缓存与 DB 结果可能不同步**：`_auto_migrate`（`database.py:200-207`）优先使用该缓存；CLI 重算（`compute_c3_mass.py:530-548`）不会回写 SQL。
12. **测试覆盖缺口大**（见 §4）：models 层 CRUD/往返、import_service、全部 routes、`find_rockets_for_orbit`/`find_rockets_for_mission`、并联级计算路径、`save_computed_data`、slugify、迁移初始化均无测试。
13. **`draftToYaml` 硬编码占位校准点**（`import.js:116-119`，payload=1/dv=0 三行）与默认任务（`editor.js:8-14`）：用户若不做修改直接保存，会产生"假校准点"入库的风险（校验只要求 3 个不同 payload）。
14. **`compute_rocket` 结果不含 `multi`/`parallel`**（`compute_c3_mass.py:316-321`）：`data_json` 无逐级可重启性，任何依赖 `GET /api/data/<slug>` 的客户端拿不到该信息（当前只有编辑器走 `GET /api/rockets/<slug>` 能拿到）。

---

## 4. 现有测试覆盖情况（问题 6 的结论）

运行命令：`python -m pytest tests/ -v`（Python 3.12.10 / pytest 9.0.3 / Windows）。**结果：9 通过，0 失败，0.24s**（测试均为 `unittest` 风格）。

| 文件 | 用例 | 覆盖内容 |
|---|---|---|
| `tests/test_import_pipeline.py` | 3 | legacy numpy 标签 YAML 安全读取+校验通过；`parse_craft` 保留多发动机组（`VAB/1T_Test.craft`，断言 6 台）；`compute_rocket` 接受多发动机组 |
| `tests/test_orbit_mission.py` | 6 | 逆行 GEO 预算数值；圆轨道 > 转移注入 Δv；点火次数/重启需求计算；顺行 vs 逆行；dv 曲线反插值（`interpolate_payload_for_dv`） |

**未覆盖**：

- `server/models.py`：`create_rocket/update_rocket/delete_rocket`、SQL↔YAML 原子同步、`get_rocket_config` 往返、`save_computed_data`、`find_rockets_for_orbit`（排名/重启筛选/feasible/reason）、`find_rockets_for_mission`、`slugify`、`interpolate_payload`。
- `server/import_service.py`：preview/confirm、SHA-256 去重、.loadmeta 配对、`.craft.original` 跳过、同名更新开关。
- 全部 `routes_*.py`（无 API 测试）。
- `compute_c3_mass.py` 的**并联级路径（L126-185）**、二次损失模型标定（L187-204）、payload_for_dv 二分（L224-242）、`compute_orbit_c3` 兼容包装。
- 数据库初始化/迁移/引擎导入（`database.py`）。
- 前端无任何自动化测试（阶段 5 计划中为浏览器回归）。

---

## 5. 后续阶段不能破坏的兼容约束

1. **级序语义**：`stage_index` 0 = 第一级，最大索引 = 末级；任何新代码不得假定与 KSP `istg` 编号一致（方向相反）。`find_rockets_for_orbit` 的 `ORDER BY stage_index DESC LIMIT 1` 语义不得改变。
2. **YAML 契约**：级段必须继续支持 `wet_mass_t / dry_mass_t / engines{cfg,count} / engine_groups / multi_ignition / parallel` 双字段并存（`docs/VAB_IMPORT_DESIGN.md:76-79`）；`calibration ≥ 3`；legacy `!!python/object/apply` numpy 标签必须继续被 `yaml_codec.LenientSafeLoader` 安全丢弃。
3. **API 兼容**：`/api/mission-query` 的 `results` 字段（`models.py:671-683`：rocket/slug/capacity/margin/feasible/supports_restart/reason 等）与 `mission` 对象（`compute_c3_mass.py:452-494`）为前端契约，阶段 4 只允许新增字段；`/api/rockets/<slug>` 的 cfg 结构（`get_rocket_config` 输出）是编辑器契约。
4. **multi_ignition 布尔列必须保留**：阶段 2-4 的逐级模型只能作为补充，不能删除 `stages.multi_ignition` 及其在 YAML 中的布尔字段（旧客户端依赖）。
5. **写路径原子性**：SQL 与 YAML 必须保持同事务/同回滚（`create_rocket`/`update_rocket`/`save_computed_data` 现有模式），新写入路径必须沿用。
6. **迁移幂等性**：`init_db` 每次启动重跑迁移（无版本号），新迁移必须 `CREATE ... IF NOT EXISTS` / `INSERT OR IGNORE` 风格，且 005 之前的迁移语句不得修改。
7. **计算与存储的级数差异**：`computation_results.data_json.stages`（展开后）与 `stages` 表（展开前）本来就不一致 —— 任何阶段不得假设二者一一对应；阶段 3 的逐级求解器必须明确自己消费哪种表示。
8. **测试入口**：`python -m pytest tests/`（unittest 风格），新测试必须与现有测试共存并保持全绿。

---

## 6. 阶段 2 所需的最小接口建议

阶段 2 只做纯 Python 数据模型（不接 Flask/SQLite/前端，不改现有 API）。建议：

1. **独立模块**：项目根 `mission_simulation.py`（纯 Python，不 import flask/sqlite/models，不读写文件）。
2. **数据结构（dict 或轻量 dataclass，必须可 JSON 序列化）**。注意职责边界：**首次点火、是否需要重启、是否允许重启属于求解结果或级状态，不得预先写进 BurnRequirement**（见 `docs/MISSION_SIMULATION_DATA_CONTRACT.md`）：
   - `BurnRequirement`（任务输入）：`{burn_id, order, name, target_dv_m_s, source, preferred_stage_index(可空), metadata}`。
   - `StageState`（某一时刻的级状态）：`{stage_index, name(可空), wet_mass_t, dry_mass_t, propellant_remaining_t, isp_vac_s, multi_ignition, ignition_count, attached, parallel, metadata}`。
   - `BurnResult`（单次燃烧结果）：`{burn_id, order, stage_index(可空), target_dv_m_s, actual_dv_m_s, mass_before_t, mass_after_t, propellant_consumed_t, propellant_remaining_t, ignition_kind(first/restart/unassigned), success, failure_code, failure_reason_zh, data_quality}`。
   - `MissionSimulationResult`：`{rocket_ref, payload_t, burns[], success, failure_code, failure_reason_zh, data_quality, warnings[], metadata}`。
   已按本契约落地为项目根目录的 `mission_simulation.py`（阶段 2 交付物）。
3. **失败码用稳定字符串枚举**（如 `propellant_insufficient` / `restart_unsupported` / `no_stage_assigned` / `data_insufficient`），并附中文说明；测试断言码字符串本身。
4. **输入标准化纯函数**：`normalize_stages(cfg_stages) -> list[StageState]`，只读取 YAML 契约字段（见 §5-2），**不修改原输入**（返回新对象）。**阶段 2 未实现**（本阶段仅数据结构 + `from_dict` 兼容普通 dict 输入），留待阶段 3 求解器接入时提供。
5. **燃烧序列来源**：`compute_orbit_mission` 的 `burn_sequence`（`compute_c3_mass.py:413-430`）+ `post_parking_ignitions`（L432）即可生成 BurnRequirement 列表（停泊轨道插入由校准覆盖，`source='rocket_calibration'` 的首项不计入重启需求）——阶段 2 可定义一个 `burn_sequence_to_requirements()` 转换函数，但**不得改动 `compute_orbit_mission` 的现有返回值**。
6. **不重复实现火箭方程**：propellant 消耗计算留给阶段 3；阶段 2 只定义状态容器与成功/失败语义。
7. **测试至少覆盖**：数据结构创建与 JSON 序列化往返；首次点火与重启的区分；失败码稳定性；输入不被原地修改（深拷贝断言）。

---

## 7. 数据库迁移状态与兼容性（问题 7 的结论）

- 迁移文件：`migrations/001_initial_schema.sql`（14 表 DDL）→ `002_seed_standard_missions.sql`（12 条种子任务）→ `003_add_stage_parallel_and_indexes.sql`（5 个索引；`parallel` 列由 Python 侧 `_ensure_stage_parallel_column` 兜底，`database.py:29-33`）→ `004_dedupe_standard_missions.sql`（删除重复 + 唯一索引）→ `005_import_sources.sql`（导入来源表）。
- 执行方式：`init_db`（`database.py:124-142`）**每次启动按序全量重跑**，全部语句幂等；`_ensure_indexes`（L36）同样幂等。`user_version` 从未更新（恒 0）。
- 已确认状态：全部 14 表存在；`stages.parallel` 列存在；`idx_standard_missions_unique` 存在；12 条标准任务无重复；`import_sources` 表存在且为空。
- 潜在兼容问题：
  - 无版本号，新迁移只能追加且必须幂等（见 §5-6）。
  - `004` 的 `DELETE` 在每次启动都会执行（幂等但属于启动时数据清理）；`create_standard_mission`（`models.py:518`）没有重复预检，撞唯一索引时返回 409（可接受）。
  - 引擎表只在空时导入（`database.py:146-149`）；新增 .cfg 依赖手动 reload。
  - 两个 slugify 实现（见问题 7）。
  - `_repair_missing_stage_engines` 在 `stage_engines` 非空时直接早退（`database.py:58-60`），存量火箭若曾以"无引擎链接"状态入库将不再自动修复（当前库无此情况）。

---

## 8. 审查结论

- 现状是"单一事实来源（SQLite）+ 写穿 YAML 备份 + .craft 半自动导入"的可用系统，9 项测试全绿，数据自洽（1 枚火箭、74 引擎、12 任务）。
- 对阶段 2-4 最关键的事实：**末级 = 最大 stage_index**；**multi_ignition 目前只是末级布尔筛选，且计算结果的 stages 不携带该字段**；**并联级会在计算层展开成虚拟阶段**，逐级模型必须明确消费哪种级表示。
- 本次审查未修改任何文件、未写入数据库。发现的全部问题见 §3，留待后续阶段处理。
