# KSP 安装配置索引（阶段 6B.1）

## 为什么优先使用 ModuleManager.ConfigCache

KSP 的最终配置不是原始 `.cfg` 之和：ModuleManager 会按依赖顺序应用大量补丁
（`@PART[*]`、`+PART`、模块增删、数值改写等）。本阶段**不**自行实现 MM 补丁
语法，也不遍历合并原始 `.cfg`。`GameData/ModuleManager.ConfigCache` 是
ModuleManager 启动时输出的"补丁应用后最终配置"，直接读取它就能得到与游戏内
一致的基础质量、密度、布局、推力等权威证据，且格式就是普通 ConfigNode 文本。

索引构建流程：

```text
KSP 根目录（调用方显式提供）
        |
        v
GameData/ModuleManager.ConfigCache（只读）
        |
        v  专用宽容解析（ParseOptions: 允许匿名节点 / 64 MB / 150,000 节点）
结构树
        |
        v
PartRecord / ResourceRecord / SstuEngineLayoutRecord + 诊断
        |
        v
KspIndex（内存，或原子写入调用方指定 cache_path 的 JSON 缓存）
```

### ConfigCache 可能陈旧

`ModuleManager.ConfigCache` 只在 KSP 启动时重新生成。用户改过 mod、打过新补丁
但没重启过 KSP，缓存就会与安装内容不一致。因此：

- 缓存/索引必须携带源文件 fingerprint（路径、大小、mtime_ns、SHA-256），
  任一变化即失效重建；
- 缺失时返回明确 `cache_missing` 状态，提示先启动一次 KSP 生成缓存，
  **不**静默退回原始 cfg 合并；
- 任何下游结论都应把"基于 ConfigCache"作为证据标注。

## 严格 craft 模式与宽容索引模式

`server/ksp_confignode.py` 的 `parse(text, options=None)` 现在接受
`ParseOptions`，默认即严格 craft 模式，**行为完全不变**：

| 能力 | 严格模式（craft，默认） | 宽容模式（ConfigCache 索引） |
| --- | --- | --- |
| 匿名 `{ ... }` 节点 | 拒绝（`missing_node_name`） | 允许，记入 `anonymous_node` 诊断 |
| 输入大小 | 25 MB | 64 MB |
| 节点上限 | 50,000 | 150,000 |
| 嵌套深度 | 64 | 64（同左） |
| 节点声明状态机 | 同左 | 同左（节点名后仍只允许空白/注释/`{`） |

匿名节点在索引中以 `name == ''` 的普通 `ConfigNode` 表示（不伪造成任何具名
节点），其内 `name = FXModuleLookAtConstraint` 只是它的普通字段。真实
ConfigCache（约 15.5 MB）含 72 个匿名节点，全部被计数并汇总为一条诊断。

## 索引数据模型

`KspIndex.to_dict()` 输出：

```json
{
  "schema_version": 1,
  "status": "ok | cache_missing | parse_error | cache_invalid",
  "metadata": { ... },
  "parts": [...],
  "resources": [...],
  "sstu_layouts": [...],
  "diagnostics": [...]
}
```

### 元数据

`schema_version`、`source_kind`（固定 `module_manager_config_cache`）、
`ksp_root`、`config_cache_path`、`file_size`、`mtime_ns`、`sha256`、
`patched_node_count`（读取文件头部的 `patchedNodeCount`）、`built_at`、
`status`、`record_counts`（url_configs / parts / resources / sstu_layouts /
anonymous_nodes / duplicate_definitions）。

### PartRecord

`name`、`title`、`manufacturer`、`description`、`category`、
`mass_raw`/`mass_t`、`cost_raw`/`cost`、`parent_url`、`cache_order`、
`primary`、直接 `RESOURCE` 列表、直接 `MODULE` 名称列表、
`ModuleFuelTanks` 的 `volume` 与 `TANK` 证据、`ModuleEngines*` 的
`engineID`/`maxThrust`/`minThrust`/`PROPELLANT`/`atmosphereCurve` key 列表
（重复 key 保留）、`SSTUModularEngineCluster` 的默认
`currentEngineLayoutName`/`engineModelName`、`dynamic_mass_modules`（见下）、
`diagnostics`。

### ResourceRecord

`name`、`density_raw`/`density_t_per_unit`、`unit_cost_raw`/`unit_cost`、
`flow_mode`、`transfer`、`parent_url`、`cache_order`、`primary`、
`diagnostics`。数值无法解析时**保留 raw 且 parsed 为 null**（不是 0），并写
`numeric_parse_failed` 诊断。

### SstuEngineLayoutRecord

`name`、`position_count`（直接 `POSITION` 数量）、`positions`（每个 POSITION
的原始字段）、`mount_size_mult_raw`/`mount_size_mult`、`mount_positions`、
`rotate_engines`、`parent_url`、`cache_order`、`primary`、`diagnostics`。

**布局实际台数 = POSITION 数量**，不再使用硬编码回退表（实测本机：
Single=1、Double=2、Triple=3、Quad=4、SixRadial=6、SixCenter=6）。

## 重复定义策略

同名同类型记录**全部保留**（按 `cache_order` 缓存顺序输出），每条记录带
`parent_url` 与 `cache_order`；从第二条起产生 `duplicate_definition` 诊断。

primary 选择规则（确定性、稳定，写入文档）：

1. 候选项按 `(parent_url 不区分大小写, cache_order)` 排序；
2. 第一个标记为 `primary`；
3. 这是查询的**确定性默认值**，不是正确性断言——重复定义仍应在 6B.2
   匹配时逐条审计。

`find_parts(name)` 返回全部候选项（不去重）；`primary_part(name)` 返回
primary；`find_resource` / `find_sstu_layout` 返回各自 primary。
`lookup_status(kind, name)` 区分 `found` / `missing` / `ambiguous` /
`invalid_record`（缺 name 等必填字段的记录视为 invalid_record），
missing 不会被自动转换成猜测结果。

## Fingerprint 与缓存失效

- `cache_path=None`：只构建内存索引，不写任何文件；
- 指定 `cache_path`：原子写入（同目录临时文件 + `os.replace`，测试断言无
  `.tmp` 残留）；
- 缓存 JSON 内含 `schema_version` 与源文件 fingerprint（路径、大小、
  mtime_ns、SHA-256）；
- `load_or_build_ksp_index`：缓存 schema 或 fingerprint 不匹配 → 重建并
  重新写缓存；
- 缓存是调用方指定路径的产物，**不**作为项目源数据提交；
- 本阶段不写 SQL，不生成火箭 YAML。

## 哪些只是证据，不能直接用于 craft 最终质量

- `mass_t`/`cost`：部件的**配置基础值**；动态模块（SSTU 布局、ProceduralParts
  尺寸、`ModuleFuelTanks`/`SSTUModularFuelTank` 燃料负载、`TweakScale`/
  `B9PartSwitch` 变体等，见 `dynamic_mass_modules`）会改变实例质量，且
  craft 中 `modMass` 只是存档时刻修正量；
- `sstu_cluster.layout`：**默认**布局，craft 实例可能保存了
  `currentEngineLayoutName` 覆盖；
- `atmosphereCurve`/`maxThrust`：配置证据，RealFuels 等补丁可能改写实例行为。

## 查询接口

```python
index.find_parts('SSTU-SC-ENG-Merlin-1B')   # 全部候选项（缓存顺序）
index.primary_part('SSTU-SC-ENG-Merlin-1B')  # 确定性 primary 或 None
index.find_resource('LiquidFuel')            # primary 或 None
index.find_sstu_layout('Triple')             # primary 或 None
index.lookup_status('part', name)            # found/missing/ambiguous/invalid_record
index.diagnostics_summary()                  # 按严重级别与错误码汇总
```

## 阶段 6B.2 计划（未实现）

把 craft 的 PART 实例（`part` 字段去掉实例 ID 后缀后的基础名）与本索引匹配：

1. 用 `find_parts` 找到所有候选定义；
2. 校验实例中的 `MODULE` 集合与索引定义一致，处理 `sym`/`link` 与 SSTU
   布局覆盖；
3. 结合 `ModuleFuelTanks`/`RESOURCE` 负载与资源密度计算质量（仍属 6B.2，
   本阶段禁止）。
