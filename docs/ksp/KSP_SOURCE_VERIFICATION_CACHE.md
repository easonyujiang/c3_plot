# KSP 源验证缓存与导入预览性能

更新：2026-10-24。本文记录 `verify_sources` 的性能问题、修复方式和仍然保留的边界。

## 问题

`server/editor_snapshot.py::verify_sources` 每次都会递归扫描整个
`GameData` 目录，比较当前 DLL 集合，再逐个哈希快照记录的文件。在本机 WSL
的 `/mnt/d` 上，单次验证约 **66–83 秒**，其中：

| 子步骤 | 实测 |
| --- | ---: |
| `GameData.rglob('*.dll')` 递归扫描（16,089 文件 / 1,875 目录） | 53–76 s |
| 哈希 118 个 DLL（29 MB） | 2.1 s |
| 哈希 `ModuleManager.ConfigCache`（14.8 MB） | 0.1 s |

一次导入预览会验证 **3 次**：`read_snapshot` 一次，`validate_rocket` 对两个
带 `runtime_performance` 的级各一次。实测 195–245 秒。

## 修复

### 1. 同一份已验证来源只验证一次

`read_snapshot` 已经验证了快照的 `source`；`preview_craft` 把它传给
`validate_rocket(..., verified_source=...)`，各级的
`checked_runtime_performance` 只在来源不同时才重新验证。来源按
`ksp_root + files` 判断，不按对象身份。

### 2. 进程内/跨进程的验证缓存

`output/cache/ksp_source_verification.json` 保存成功验证过的环境指纹：

- 缓存键 = `ksp_root` + 快照记录的全部 `(path, sha256)`；
- 缓存命中条件 = 当前“来源签名”与缓存一致，且未超过 6 小时 TTL；
- 来源签名覆盖：
  - 记录文件的 `(size, mtime_ns)`；
  - `GameData` 本身及其全部一级目录；
  - 每个记录文件的父目录和祖先目录（含 `Plugins` 等插件目录）。
- 签名变化 → 丢弃缓存，执行完整扫描和哈希；失败不写缓存；
- 缓存文件缺失、损坏或 TTL 过期 → 完整验证；
- 写入使用临时文件 + `os.replace`，缓存不可写时不影响验证。

### 3. 保留完整校验作为权威路径

缓存只缓存**成功**结果；命中时仍检查所有记录文件的 stat 和上述目录的
mtime。任何记录文件变化、新顶层 Mod、或在已记录插件目录旁新增 DLL 都会
使签名变化并触发完整校验。完整校验仍使用原有哈希比较，不降低判定标准。

## 实测结果

使用 `C3 Neutrons I 2630 Payload Baseline.craft`（2 个 runtime 级）：

| 场景 | 修复前 | 修复后 |
| --- | ---: | ---: |
| 冷启动首次预览（缓存缺失） | 195–245 s | **71–73 s**（1 次扫描） |
| 同进程第二次预览 | 195–245 s | **2.75 s**（签名命中） |
| 新进程、缓存已存在 | 195–245 s | **3.96 s**（签名命中） |
| 保存/任务查询的单级验证 | 66–83 s/级 | 约 3 s/级（命中） |

冷启动仍要付一次完整扫描；这是本机 WSL 9p 文件系统上递归遍历
`GameData` 的成本，不是哈希或解析成本。

## 边界与手动刷新

- 缓存是本地单用户工具的加速层，不是防本地篡改的安全边界；签名使用
  size/mtime，不保证检测“同 size + 同 mtime”的内容替换。需要最强保证时
  删除缓存或调用 `clear_source_cache()` 强制完整校验。
- 签名覆盖一级目录和记录路径的祖先；如果新 DLL 出现在一个既不是记录路径
  祖先、也不在一级目录的深层空目录中，6 小时 TTL 到期后的完整扫描会捕获。
- 强制刷新：

```bash
.venv/bin/python -c "from server.editor_snapshot import clear_source_cache; clear_source_cache()"
```

或直接删除 `output/cache/ksp_source_verification.json`。

## 测试

`tests/test_runtime_workflow.py` 新增覆盖：

- 第二次验证不再调用 `_scan_plugin_paths`；
- 在已记录插件目录旁新增 DLL 会使缓存失效并拒绝；
- `validate_rocket` 会把 `verified_source` 传给
  `checked_runtime_performance`；
- 原有的文件修改和顶层新 DLL 失效测试继续通过。

当前完整回归为 **938 项通过、0 失败、0 错误、81 跳过**。
