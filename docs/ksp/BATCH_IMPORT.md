# 批量导入 API

当前 React 工作台提供游戏 craft 发现与单箭导入。批量文件导入仍可通过后端 API 使用；旧界面的批量上传面板已随旧界面移除。

1. 调用 `POST /api/import/batch/preview` 预览多个 YAML、craft 及配对 loadmeta。
2. 检查返回的 `items`，排除 `needs_review` 和 `error` 项，补齐质量、分级与校准。
3. 将确认的项目提交到 `POST /api/import/batch/confirm`。`allow_updates` 必须是布尔值。

预览只读。来源 SHA-256 用于幂等识别，craft.original 被忽略，loadmeta 只作辅助来源；同名但不同内容的配置是否更新由确认参数决定。确认后按常规计算、数据库与 YAML 同步路径写入。

保留 `engine_groups` 和实际发动机集群数量。未知质量或分级状态不能因批量导入而跳过验证。旧 NumPy YAML 的读取兼容仅处理历史缓存标签，不执行 Python 对象；写出只使用普通 YAML 数值。

具体请求/返回结构以 `server/routes_import.py` 和 `server/import_service.py` 为准，错误与来源语义见 [VAB 导入设计](VAB_IMPORT_DESIGN.md)。
