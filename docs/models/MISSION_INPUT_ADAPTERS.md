# 任务仿真输入适配层（Mission Simulation Input Adapters）

> 示例说明：`rockets/neutron_i_2630.yaml` 是 2026-10-21 清零前的样例路径，
> 当前工作树不再包含它；适配规则本身仍适用。

阶段 3D 交付物：`c3_mass/mission_adapters.py`（c3_mass 包目录，纯 Python）。

## 1. 定位与边界

适配层连接计算层与任务仿真层，提供两个转换：

1. `normalize_stages(cfg_stages) -> StageNormalizationResult`：原始火箭配置级 → 一一对应的 `StageState`。
2. `burn_sequence_to_requirements(burn_sequence, ignition_threshold_m_s=0.1) -> BurnSequenceConversionResult`：`compute_orbit_mission` 的 `burn_sequence` → `BurnRequirement` 列表。

依赖：标准库 + `c3_mass/mission_simulation.py` + `compute_c3_mass.resolve_stage_engine_performance`。不依赖 Flask / SQLite / `server.models` / 前端 / .craft-VAB 解析器；不读写文件。

**本阶段不实现**：停泊轨道推进剂回放、自动执行级分配（所有 `preferred_stage_index` 均为 None）、分离计划生成、`simulate_multistage_sequence` 调用、API 接入。

## 2. 配置级与计算层虚拟级的区别

- **原始配置 stages（存储级）**：`rockets/*.yaml` 或编辑器保存的 `stages` 列表，顺序为从下到上，**列表位置即规范 `stage_index`（0、1、2……）**。
- **计算层虚拟级**：`compute_rocket` 返回的 `result['stages']` 可能经过**并联级虚拟展开**（`compute_c3_mass.compute_rocket` 的 parallel 分支会把"助推+芯级"合成相位再拆出），其级数与质量**不能**与原始配置一一对应。

**为什么 StageState 必须来自原始配置级**：逐级仿真需要真实级的推进剂、点火次数与分离语义；虚拟级是运力计算的中间产物，把它们当作物理级会导致推进剂/质量回放错误。因此：

- `normalize_stages` 只消费原始配置 `stages`。
- **本阶段不得使用 `result['stages']` 反向构造 StageState**，也不得将两者强行配对。

## 3. normalize_stages 规则

- 输入必须是非空 list/tuple，每项必须是 dict；顺序保留，禁止排序。
- `stage_index` = 列表位置，**不使用计算层虚拟展开级编号**。
- 每个 `StageState`：

| 字段 | 来源 |
|---|---|
| `wet_mass_t` / `dry_mass_t` | 原配置值 |
| `propellant_remaining_t` | `wet_mass_t - dry_mass_t`（全满状态） |
| `isp_vac_s` | `resolve_stage_engine_performance` 的综合性能（多发动机组合并，不只看第一个） |
| `multi_ignition` / `parallel` | 原配置值 |
| `ignition_count` / `attached` | 0 / True |
| `metadata` | `cfg_index`、`engine_cfg`（cfg 文件名列表）、`engine_count_total`、`performance_source` |

- **不进行并联级虚拟展开**：parallel=True 的级仍然一一对应返回，parallel 标志保留。
- **不合并/丢弃 SSTU 单部件多发动机数量**——本函数只消费现有规范配置（`engines`/`engine_groups`）。

### 3.1 parallel=True 为什么暂时只能返回 INSUFFICIENT

并联级需要"助推器 + 芯级"组合求解（单级原语与串联求解器均不支持，`execute_burn` 对 parallel 级返回 `DATA_INSUFFICIENT`）。因此只要任一阶 parallel=True：

- `ready_for_serial_replay=False`
- `data_quality=INSUFFICIENT`
- `warnings` 明确说明"并联燃烧尚未建模"
- **不抛"配置非法"异常**——配置本身合法，只是暂不支持回放。

### 3.2 发动机数据不足

找不到 cfg / 发动机组为空 / 无法得到有效真空比冲时：

- 不伪造默认比冲 → 对应 `StageState.isp_vac_s=None`。
- `ready_for_serial_replay=False`，`data_quality=INSUFFICIENT`。
- warning 包含 `stage_index`、发动机 cfg 与原始错误原因。
- 只捕获预期的数据缺失/配置异常（`ValueError` / `FileNotFoundError`），不吞掉程序错误。

真正非法的调用参数（负质量、dry>wet、布尔值冒充质量、空输入、非 dict 条目）→ `ValueError`。

## 4. burn_sequence_to_requirements 规则

- `order` 非布尔正整数且严格递增；`source` 非空字符串；`name` 字符串；`dv_m_s` 有限非负（numpy 标量转换为 Python float）。
- **稳定 burn_id**：输入提供合法 `burn_id` 则保留；否则 `orbit_burn_{order}`；**不从中文 name 生成**；最终必须唯一（冲突抛 ValueError）。
- `source='rocket_calibration'`：**始终转换**（即使 `dv_m_s=0` 也保留为校准记录），`preferred_stage_index=None`。
- 其他来源：`dv_m_s > ignition_threshold_m_s` 才算一次点火；`<=` 阈值（含等于）不构成点火 → 写入 `skipped_entries`（`order`、`name`、`source`、`dv_m_s`、`reason='below_ignition_threshold'`）。
- 跳过项目**不重排 order**（保留原值）。
- 本阶段禁止填写 `preferred_stage_index`。
- metadata 只复制明确需要的 JSON 安全字段，不把未经校验的原始字典塞入；warnings/metadata 均为 JSON 原生类型。

### 4.1 低于点火阈值如何处理

`compute_orbit_mission` 的 burn_sequence 可能包含 0 m/s 的书签项（如无平面变化时的"平面变化点火"）。它们不是真实点火，不生成 requirement；在 `skipped_entries` 中记录，`warnings` 汇总跳过数量，供调用方审计。

## 5. 本阶段为何不分配 preferred_stage_index

执行级分配依赖"火箭在停泊轨道时的状态回放"结果（哪一级仍在、剩多少推进剂、能否重启）。本阶段尚未实现该回放，因此转换结果全部为 `preferred_stage_index=None`——下一阶段（串联火箭停泊轨道状态回放）负责在回放之后填充执行级。

## 6. 测试

`tests/test_mission_adapters.py`（23 项）：

1-4. 两级串联正确生成两个 StageState（顺序、stage_index=位置、wet/dry/prop 关系、multi_ignition 保留）。
5. 多 engine_groups 使用综合性能解析结果（独立公式验证 isp 与发动机组元数据）。
6-7. 输入完全不被修改；输出 metadata 不共享引用。
8-9. parallel 级一一对应、不展开；ready=False + INSUFFICIENT。
10-11. 发动机 cfg 缺失 → isp=None；warning 含 stage_index 与原因。
12. 非法质量 / dry>wet / 空 stages / 非 dict → ValueError。
13-17. 标准三次燃烧序列转换；calibration 保留且不分配执行级；低于阈值跳过并记录；order 不重排；burn_id=orbit_burn_{order}。
18. 重复/非法/倒序 order、NaN/Infinity → ValueError。
19. numpy.float64 → Python float。
20. 两种结果可 json.dumps。
21. 转换前后输入完全不变。
22. 真实 `compute_orbit_mission(35786, 180, combined)` → 1 校准 + 2 真实，preferred 全 None。
23. `arrival_strategy='separate'` 且无平面变化 → 0 m/s 平面变化项不算点火。
24. `rockets/neutron_i_2630.yaml` 只读集成 → 两个串联 StageState、第二级 multi=True、ready=True、不读取计算层虚拟 stages。

期望值使用独立公式或人工常数，不调用被测函数生成。

## 7. 下一阶段（本阶段不得实现）

- **串联火箭停泊轨道状态回放**：推算火箭抵达 220 km 停泊轨道时各串联级剩余的推进剂（`ignition_count` 是否表示入轨点火由回放层决定），并据此填充执行级。
- 自动执行级分配、自动分离计划。
- `simulate_multistage_sequence` 接入。
- API / 数据库 / YAML / 前端接入。
