# KSP PART 匹配（阶段 6B.2）

本阶段实现 `.craft` 中每个 PART 实例与 KSP 安装配置索引（阶段 6B.1 的
`server/ksp_install_index.py`）之间的**确定性匹配**，并生成可审计的匹配报告。

本阶段**没有**进行质量、推进剂、级间拓扑、发动机数量、Delta-V 或轨道运力
求解——匹配结果只回答"这个实例对应安装索引中的哪个定义"。

## craft 保存名称为什么可能使用点号

KSP 存档（`.craft`）中 PART 的 `part` 字段是"部件内部名_实例ID"。极少数
情况下 KSP 会把部件名称中的点号写进存档（历史上名称带 `.` 的部件在旧存档
或特定场景下会以点号保存），而 ModuleManager.ConfigCache 中的名称总是使用
下划线。实测本机 VAB 20 个 craft、389 个实例中有 8 个实例受影响：

| craft 名称 | 实例数 | 索引名称 |
| --- | --- | --- |
| `probeCoreOcto.v2` | 4 | `probeCoreOcto_v2` |
| `mk1pod.v2` | 3 | `mk1pod_v2` |
| `Decoupler.1` | 1 | `Decoupler_1` |

## 本项目采用的 `.` → `_` 确定性规则

唯一的名称还原规则是：**将 craft 名称中的每个 `.` 替换为 `_`，然后再次进行
大小写敏感的完整名称匹配**。仅在原始名称**完全没有候选**时才尝试；仅当还原后
名称恰好有唯一候选时才算匹配成功。

```text
原始 base_part（大小写敏感、完整匹配）
  ├─ 唯一候选 → exact
  ├─ 多个候选 → ambiguous（绝不自动选取 primary）
  └─ 无候选 → 若名称含 '.'，全部 '.'→'_' 后再次完整匹配
                ├─ 唯一候选 → normalized_unique
                ├─ 多个候选 → ambiguous
                └─ 无候选   → missing
```

## 为什么禁止模糊匹配

安装索引中同时存在 `probeCoreOcto_v2` 和 `probeCoreOcto2_v2`。任何前缀、
包含、子串、编辑距离或"看起来最像"的匹配都会把 `probeCoreOcto.v2` 错配到
`probeCoreOcto2_v2`。本阶段匹配器严格禁止：

- 编辑距离 / 前缀 / 后缀 / 子字符串匹配；
- 部件 title 匹配；
- 自动大小写不敏感匹配（大小写相近名称只作为诊断建议，绝不自动选中）；
- 删除 `.v2` 后缀、`_` 反向转换为 `.`；
- 用 `primary_part` 掩盖重复定义或歧义。

## 状态与策略

实例状态 `status`：

- `exact`：原始名称唯一候选；
- `normalized_unique`：原始名称无候选，`.` → `_` 后唯一候选；
- `ambiguous`：原始或还原名称存在多个候选，`selected` 为空，
  `candidates` 保留全部，并明确说明后续质量计算不能安全使用该实例；
- `missing`：两种名称都无候选（`no_candidates` 诊断；如存在仅大小写不同的
  候选，附 `case_variant_suggestion` 仅供参考）；
- `invalid_part`：`base_part` 为空或无效，不做任何猜测；
- `index_unavailable`：没有可用索引（不崩溃，预览降级为警告）。

策略 `strategy`：`exact_name`、`dot_to_underscore`、`none`。

每条实例记录包含：`craft_index`、`part`、`base_part`、`instance_id`、
`persistent_id`、`requested_name`、`canonical_name`、`status`、`strategy`、
`confidence`（high/medium/low/none）、`candidates`、`selected`、
`diagnostics`。候选与 selected 保留 `name`、`parent_url`、`cache_order`、
`primary` 与候选诊断代码。

## 匹配报告如何供下一阶段使用

`match_craft_parts(craft_draft, ksp_index) -> report`：

```json
{
  "schema_version": 1,
  "source_kind": "ksp_part_matcher",
  "index_fingerprint": { "config_cache_path": "...", "file_size": ...,
                         "mtime_ns": ..., "sha256": "...", ... },
  "total_instances": 389,
  "exact_instances": 381,
  "normalized_instances": 8,
  "ambiguous_instances": 0,
  "missing_instances": 0,
  "invalid_instances": 0,
  "matched_instances": 389,
  "coverage_ratio": 1.0,
  "unique_part_types": 66,
  "matched_unique_part_types": 66,
  "unique_part_type_coverage_ratio": 1.0,
  "by_status": { "exact": 381, "normalized_unique": 8, ... },
  "instances": [ ...每实例记录... ]
}
```

实例覆盖率和唯一部件类型覆盖率**分开统计**。`enrich_craft_draft(draft,
ksp_index)` 返回草稿的**深拷贝**，附加 `installation_match` 键并把
missing/ambiguous 实例生成为 `warning` 级、normalized_unique 生成为 `info`
级结构化警告；原草稿与索引绝不被修改。阶段 6B.3 将只对 `status` 为
`exact` / `normalized_unique` 且 `selected` 唯一的实例提取部件干质量、
资源容量与来源证据。

## 预览流程接入

`preview_craft(filename, content, loadmeta=None, ksp_index=None)`：

- 不传 `ksp_index` 时，通过惰性 provider（`get_auto_ksp_index`）取得索引：
  优先 `C3_MASS_KSP_ROOT` 环境变量，其次 `config.KSP_INSTALL_CANDIDATES`，
  不扫描磁盘；进程内按源 fingerprint（路径 + 大小 + mtime_ns）缓存；
- 索引不可用时导入**不崩溃**，返回 `index_unavailable` 诊断（warning 级
  结构化警告），所有实例 `status=index_unavailable`；
- 索引可用时把匹配摘要（`installation_match`）与 missing/ambiguous 结构化
  警告附加到预览结果；批量预览对整批共享一次索引；
- 不针对每个 PART 实例重复构建索引。
