# VAB `.craft` 导入设计

## 1.1 ConfigNode 结构解析基础层（阶段 6A）

`server/ksp_confignode.py` 提供无第三方依赖、确定性的 KSP ConfigNode 解析器：

- `ConfigNode`（name、有序 values 且保留重复键、有序 children、start_line/end_line）
- `ConfigValue`（key、value、line）
- `CraftParseError`（稳定 `code`、中文 `message`、`line`/`column`、`node_path`）

支持 UTF-8 BOM、CRLF/LF、空行、缩进/Tab、`//` 行注释、任意深度 `{}` 嵌套、重复字段（`sym`、`attN`…）与重复子节点、`=` 后含空格/逗号的任意值；带最大嵌套深度（64）、最大节点数（50,000）与输入大小（与导入层一致的 25 MB）保护；解析不修改输入。

### ConfigNode 节点声明状态规则（阶段 6A.1）

解析器在读到节点名后进入"待 `{`"状态：此后**只允许**空白、`//` 注释与 `{`。遇到以下情形一律返回稳定 `CraftParseError`：

| 输入 | 错误码 | 说明 |
| --- | --- | --- |
| `PART\n`（EOF） | `dangling_node_name` | 悬空节点名：名称后应紧跟 `{` |
| `PART\n}` | `dangling_node_name` | 名称后直接遇到 `}` |
| `PART\nship = X\n{\n}` | `invalid_node_declaration` | 节点名后出现赋值 |
| `A\nB\n{\n}` | `invalid_node_declaration` | 节点名后出现另一个名称 |
| 非 str/bytes 输入 | `invalid_input_type` | 稳定解析错误，不泄漏 AttributeError |
| 非法 UTF-8 bytes | `invalid_encoding` | 解码失败转为稳定错误 |
| `{` 前无名称 | `missing_node_name` | 原有行为 |
| `=` 前无键名 | `invalid_assignment` | 原有行为 |

所有错误携带 `line`、`column`、`node_path`（尽量）；`CraftParseError` 构造过程绝不抛出 ValueError（不再校验错误码），但解析器内部只使用 `_ERROR_CODES` 中已注册的错误码（测试覆盖每个触发路径）。

### 注释识别规则（阶段 6A.1）

`//` 只有在**合法注释位置**才作为注释：行首位置（前置空白后）或值内前一个字符是空格/Tab 时。因此：

- `istg = 1 // inline comment` → 值为 `1`，注释被剥离；
- `url = http://example/path` → 值为 `http://example/path`，**不被截断**；
- `url2 = https://x.org//a` → 完整保留。

### 统一错误结构（阶段 6A.1）

单文件与批量预览对文件大小、编码与 ConfigNode 格式错误使用同一错误结构（批量条目额外带 `file`/`status`/`action`）：

```json
{
  "error": "面向用户的信息",
  "error_code": "stable_code",
  "line": null,
  "column": null,
  "node_path": null
}
```

错误码：`missing_file`、`unsupported_format`、`file_too_large`、`invalid_encoding`、`invalid_input_type`、`internal_error`（兜底，不暴露异常文本或堆栈）、`no_parts_found`，以及 ConfigNode 的格式错误码（`unexpected_eof`、`unexpected_close`、`missing_node_name`、`invalid_assignment`、`dangling_node_name`、`invalid_node_declaration`、`depth_exceeded`、`node_limit_exceeded`、`input_too_large`）。

`server/vab_parser.py` 已重构为基于结构树提取"结构证据"，不再用正则切割 `MODULE { ... }`：

### `.craft` 中能直接确认的数据

- 根信息：`ship`、`version`、`description`、`type`、`_modVersions`。
- 顶层 `PART` 节点；部件名、实例 ID、`persistentId`。
- `istg`、`dstg`、`sidx`、`sqor`、`sepI`（存档分级/排序字段，**不是**已确认的物理级序）。
- `attN`、`srfN`、`link`、`sym` 等重复连接字段（原始字符串列表，按文件顺序保留、不去重；`link`/`sym` 不当作额外部件数量）。
- MODULE 名称列表；RESOURCE 的 `name`/`amount`/`maxAmount`；`ModuleFuelTanks` 的嵌套 `TANK`；`ModuleEngines*` 的 `engineID` 与 `PROPELLANT`；`MultiModeEngine` 的 `primaryEngineID`/`secondaryEngineID`；`SSTUModularEngineCluster` 的 `currentEngineLayoutName`/`engineModelName`。

### 只能作为证据的数据

- `modMass`/`modCost`：**存档时刻的模块修正值，不是部件总质量**（`mod_mass_is_delta_only` 警告）。
- SSTU 布局台数：来自内置回退表（Single=1/Twin=2/Triple=3/Quad=4/SixRadial=6/SixCenter=6），标注 `layout_confidence='low'`，并以结构化警告说明需以用户 GameData 安装为准（实测本机 `Layout-02-Double.cfg` 为 2 个 POSITION，回退表缺少 `Double` —— 已知缺口，属 6B 范围）。
- `istg` 分组：仅生成 `legacy_placeholder` 级草稿，质量留 0，`requires_mass_confirmation=true`，强制进入 `needs_review`。

### 当前无法确认的数据

- 各级湿/干质量与推进剂。
- 物理级序与分离关系。
- 真实发动机台数（SSTU 布局、RealFuels 配置需 GameData 索引）。
- 对称（`sym`）只作证据：KSP 存档已保存每个实际 PART 实例，**不**二次放大。

### 发动机识别去重规则

- SSTU 集群部件内的 `ModuleEnginesFX` 是集群实现，台数只取布局计数，不重复累计。
- `MultiModeEngine` 主/副模式是同一台发动机的切换，按 1 台计（`multimode_single_engine` 警告）。
- `ModuleEngines*` 前缀匹配覆盖 `ModuleEngines`/`ModuleEnginesFX`/`ModuleEnginesRF`（RealFuels）。
- `sym` 不参与计数。

### 结构化警告

统一形状：`{"code", "severity", "message", "part_name", "part_id", "module", "field", "impact", "details"}`；同时保留字符串 `warnings` 兼容现有预览 UI。已定义：`mass_not_confirmed`、`stage_structure_unconfirmed`、`mod_mass_is_delta_only`、`sstu_layout_unknown`、`sstu_layout_low_confidence`、`multimode_single_engine`、`symmetry_not_multiplied`、`engine_rf_config_unconfirmed`、`no_parts_found`。

#### structured_warnings 与旧 warnings 的兼容关系（阶段 6A.1）

- 导入服务（`server/import_service.py`）保留 `structured_warnings`，展示 severity、message、impact；`mass_not_confirmed` 与 `stage_structure_unconfirmed` 使用高亮样式。
- 旧字符串 `warnings` 仅作为兼容回退（structured 为空时使用），避免同一警告重复显示。
- 批量预览响应顶层新增 `warnings` 摘要：`[{file, severity, code, message}]`，供列表展示。
- `severity=error` 的警告（如 `no_parts_found`）阻断进入编辑器：单文件返回 HTTP 400，批量条目 `status=error` 且不含 `config`。
- `severity=warning` 的警告允许进入编辑器人工补全。

### 预览状态契约（阶段 6A.1）

单文件与批量预览复用同一公共解析函数 `import_service.preview_craft`，状态/动作不允许出现 `needs_review + create` 组合：

| 情形 | status | action |
| --- | --- | --- |
| 解析错误（格式/编码/大小/无顶层 PART） | `error` | `error` |
| craft 结构有效但质量未确认（`requires_mass_confirmation`） | `needs_review` | `review` |
| YAML 校验通过 | `ready` | `create` / `update` |
| 同 hash 已导入 | `duplicate` | `duplicate` |

预览接口（`/api/import/vab/preview` 与 `/api/import/batch/preview`）绝不写 SQL、不写 YAML 备份、不落盘原始 craft；`confirm` 阶段才落库。

### 下一阶段 6B（未实现）

结合用户 GameData 的部件配置（基础质量、`ModuleFuelTanks` 容积/资源密度、SSTU 布局 POSITION 数）、连接拓扑（`attN`/`srfN`/`link`）与 staging 字段（`istg`/`dstg`/`sepI`）求解物理级与质量；并以 GameData 中的布局定义替换内置回退表。


## 目标与边界

把 KSP VAB 存档转换为可编辑的火箭**草稿**，尽可能预填名称、级段、发动机和质量；用户确认质量与校准点后，才调用现有 C3-Mass 计算并写入正式火箭数据。

`.craft` 不是性能报告。它没有本项目所需的三条“载荷 / 轨道 / 剩余 dv”校准数据，也不能单靠文件文本可靠给出每个级段的干质量。因此导入器不得承诺“上传后得到精确性能曲线”。

## 已核对的 KSP / SSTU 数据

本机 KSP 1.12.5 安装中的下列文件是导入规则的权威来源：

- `GameData/SSTU/Data/EngineLayouts/Layout-03-Triple.cfg`：`Triple` 有 3 个 `POSITION`。
- `GameData/SSTU/Data/EngineLayouts/Layout-06-SixRadial.cfg`：`SixRadial` 有 6 个 `POSITION`。
- `GameData/SSTU/Parts/Engines/SC-ENG-Merlin-1B.cfg`：部件 `SSTU-SC-ENG-Merlin-1B` 的原始布局为 `Single`，额定推力 400 kN。
- `GameData/SSTU/Parts/Engines/SC-ENG-LR81-8096.cfg`：部件 `SSTU-SC-ENG-LR81-8096` 的原始布局为 `Single`，额定推力 71 kN。

因此 VAB 存档里的 `PART` 数量绝不能当作发动机数量。例如，存档中的一个 `SSTU-SC-ENG-Merlin-1B` 部件若保存了 `currentEngineLayoutName = Triple`，它实际表示 3 台 Merlin-1B；两个这样的 `PART` 则是 6 台。清零前的 `Neutrons I 2630.craft` 历史样例正是这种情况；当前应直接用游戏存档核对。

## 导入流程

```text
.craft + 可选 .loadmeta
        |
        v
解析 / 指纹去重 / 读取 PART 与 MODULE
        |
        v
草稿：级段、发动机组、资源、质量置信度、待补项
        |
        v
用户确认：级段质量、映射异常、三条校准点
        |
        v
生成 YAML -> 校验 -> 计算 -> 写入 SQLite 与 YAML 备份
```

1. 接收一个或多个 `.craft`，同名 `.loadmeta` 自动配对；`.craft.original` 只用作历史基线，绝不作为候选火箭导入。
2. 对 `.craft` 原始字节计算 SHA-256。若同一 hash 已导入，提示跳过；若同名但 hash 不同，创建“更新草稿”，不要静默覆盖正式火箭。
3. 读取顶层 `ship`、`description`、`version`、`_modVersions`；从 `.loadmeta` 读取 `partCount`、`stageCount`、`totalMass`、`partNames`。这些字段用于展示与完整性校验。
4. 解析每个 `PART` 的 `part`、`istg`、`dstg`、`link`、资源和 `MODULE`。忽略 `istg = -1` 的发射夹等非火箭级段部件，但保留其信息供审计。
5. 按点火/分离顺序形成级段草稿，保留 KSP 原始 stage 编号；不要假定编号连续，也不要仅按文件顺序排序。
6. 对每个级段构建多个“发动机组”，每组为 `{cfg, count}`。同 cfg 合并计数，不同 cfg 保持为不同组。
7. 质量模块根据资源量、KSP 部件配置和映射表估算湿/干质量，并与 `.loadmeta.totalMass` 比较。超过容差时阻止一键计算，要求用户修正。
8. 用户补齐至少 3 条不同载荷的校准点后，生成兼容当前格式的 YAML、运行 `validate_rocket`、计算性能并保存。

## SSTU 多发动机计数规则

### 规则

对每个 `PART`：

```text
实际台数 = PART 实例数 × layout_position_count × 模型/配置倍率
```

- `layout_position_count` 来自 `SSTU_ENGINELAYOUT[name]` 的 `POSITION` 数量。
- 当前 VAB 实例优先使用 `SSTUModularEngineCluster.currentEngineLayoutName`。
- 若实例没有该字段，使用 KSP 部件配置中的默认 `currentEngineLayoutName`；缺失时默认 1。
- 必须从**用户实际 KSP 安装**读取布局，不能在代码中硬编码 `Triple = 3` 等常量；SSTU 的布局可被模组更新或补丁改写。
- `ModuleEnginesFX.maxThrust` 是单台基础发动机的推力；集群总推力由实际台数相乘。项目的 `engines.count` 正是这个实际台数，不能填写 PART 数量。

### 发动机 cfg 映射

优先映射规则：

```text
SSTU-SC-ENG-Merlin-1B -> SC-ENG-Merlin-1B.cfg
SSTU-SC-ENG-LR81-8096 -> SC-ENG-LR81-8096.cfg
```

即去掉 `SSTU-` 前缀并与本项目 `engines/` 中的 cfg 文件精确匹配。没有精确匹配的部件（例如 RealFuels 的发动机配置、SRB、补丁替换过的发动机）必须列为“需要映射”，不允许猜测。

### 不同发动机混合的级段

YAML 现在同时保留兼容字段 `engines: {cfg, count}` 和完整字段
`engine_groups: [{cfg, count}, ...]`。同一等级出现不同 cfg 时，导入器不再静默丢弃第二种发动机；计算器按各组的总推力与总质量流率合成等效真空/海平面比冲，数据库 `stage_engines` 也会保存每一组。

编辑器会保留多发动机组，主发动机仍可在原有选择框中调整；如果要改变辅助组，建议直接编辑 YAML 后批量预览。无法映射的 cfg 仍然必须在草稿页修正，不能猜测。

## 质量与推进剂

`.loadmeta.totalMass` 是整箭校验值，不是级段质量表。`.craft` 中的 `modMass` 也不能直接视为部件干质量：它可能是模块修改量，且资源质量、部件基础质量、补丁与可变尺寸都可能另行影响总质量。

可靠的质量估算至少需要：

- 解析实际安装中相应 `PART` 的基础 `mass`；
- 解析 `ModuleFuelTanks` / `RESOURCE` 的 `amount`；
- 读取对应资源密度及 RealFuels 配置；
- 将资源与部件归属到正确的分离级段；
- 将计算总质量与 `.loadmeta.totalMass` 对账。

在这些数据未齐时，草稿页应显示“质量待确认”，允许用户录入每级湿/干质量；绝不能把估算值标成精确值。

## 推荐接口与保存元数据

现有导入接口分为单文件兼容接口和批量事务式接口：

- `POST /api/import/vab/preview`：仅解析，返回草稿、`status`/`action`、`warnings` 与 `structured_warnings`，不写数据库；无效 craft（含无顶层 PART）返回 HTTP 400 与统一错误结构。
- `POST /api/import/vab/confirm`：接收用户修订后的草稿，执行现有 YAML 校验/计算/保存。
- `POST /api/import/batch/preview`：一次预览混合 YAML、`.craft`、`.loadmeta`，不写数据库；响应含 `items` 与顶层 `warnings` 摘要。
- `POST /api/import/batch/confirm`：只提交预览中可用的项目，逐项计算、创建/更新并报告结果。

每次确认导入还应保存来源字段：`source_filename`、`source_sha256`、`ksp_version`、`mod_versions`、`imported_at`、`source_total_mass_t` 与 `parser_warnings`。这可以可靠处理 VAB 中的 `.original`、重名文件和重复上传。

## 验收标准

- `Triple`、`SixRadial` 等 SSTU 布局被计为 3、6 等实际台数，而非 1。
- 两个 `Triple` Merlin PART 产生 `SC-ENG-Merlin-1B.cfg × 6`。
- 同级不同型号发动机不会被悄悄合并为一种。
- 缺少三条不同载荷校准点时只能保存草稿，不能计算正式性能。
- 质量估算与 `.loadmeta.totalMass` 偏差超过配置容差时显示阻断性警告。
- 同 hash 重复上传不新增记录；变更后的同名 craft 需要用户确认更新。
