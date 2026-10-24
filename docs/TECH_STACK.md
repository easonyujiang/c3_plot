# 技术栈与开发基座

C3-Mass 是本地火箭运力工作台。选型以现有计算可信度、桌面浏览器体验、离线运行和维护成本为依据，不要求云服务。

| 层 | 选型 | 作用 |
| --- | --- | --- |
| 界面 | React 19、严格 TypeScript、Vite | 组件、类型检查、快速开发与静态交付 |
| 数据查询 | TanStack Query 5、原生 fetch | 缓存、请求取消、状态恢复、刷新与受控重试 |
| 视觉 | CSS 设计变量、Lucide 图标 | 统一配色、响应式布局、键盘焦点与减少动画支持 |
| 科学图表 | 本地 Plotly | 悬停、缩放、平移、图片导出与离线查看 |
| 服务 | Flask 应用工厂、Waitress | 复用领域 API，提供跨平台本地 HTTP 服务 |
| 计算与存储 | NumPy、SQLite、YAML | 保留计算模型与数据库迁移；运行数据从零建立 |
| 环境 | Python 3.12–3.14、uv.lock、npm lockfile | 可重复安装与独立工作区 |
| 验证 | unittest、TypeScript、Playwright | 隔离数据回归与真实浏览器交互检查 |

## 分层约束

- `c3_mass/` 实现计算，不依赖浏览器；`server/` 承担 HTTP、存储和导入；`frontend/` 负责展示和用户输入。
- 查询统一经过 `api.ts` 和 `query.ts`。默认缓存新鲜期 30 秒，非 4xx 错误最多重试一次；写入不自动重试。游戏库每 10 秒刷新，系统状态每 30 秒刷新，窗口恢复焦点时重新检查过期数据。
- 火箭配置与曲线按 slug 分别缓存，路由变化会取消无人订阅的查询；保存后更新曲线缓存并使配置和列表缓存失效。
- 编辑器、任务分析、运力图表按需加载。Plotly 从本地静态文件加载，不依赖 CDN。
- 编辑器快照的 Mod/源文件验证按来源签名缓存；签名变化、缓存损坏或超过 6 小时 TTL 时执行完整扫描，具体边界见 [源验证缓存](ksp/KSP_SOURCE_VERIFICATION_CACHE.md)。
- `tokens.css` 是颜色与字体入口；`style.css` 定义布局及组件状态。错误边界处理渲染失败，API 错误显示重试入口，空数据提供下一步操作。
- 正式启动使用 Waitress；Vite 仅供开发。默认只监听 `127.0.0.1`。

## 命令

Linux / WSL：

```bash
./scripts/setup
./c3 --doctor
./c3 --open
.venv/bin/python scripts/dev.py
make check
```

Windows 原生：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/setup.ps1
.venv/Scripts/python.exe -m c3_mass --doctor
.venv/Scripts/python.exe -m c3_mass --open
.venv/Scripts/python.exe scripts/dev.py
```

开发服务使用 `.env` 中的 `C3_MASS_PORT`，Vite 在 `http://127.0.0.1:5173/workspace/`。用 `C3_API_URL` 可覆盖开发代理目标。`scripts/dev.py` 同时启动和停止两个服务。先完成静态构建才能启动后端。

`--doctor` 只检查 Python、依赖、静态构建与配置的游戏目录，不启动服务、不初始化数据库。KSP 未配置不会妨碍浏览已有档案。

Python 依赖以 `pyproject.toml` / `uv.lock` 为准，`requirements.txt` 是 `uv export --frozen --no-hashes --no-dev` 的兼容导出。更改依赖时同步 lockfile 和导出文件。前端以 `package-lock.json` 为准，使用 `npm ci`。

## 浏览器检查

浏览器仅用于自动检查，日常运行和构建无需下载测试浏览器。若运行环境有可供 Playwright 驱动的 Edge，可设置 `C3_BROWSER_CHANNEL=msedge`；只有没有兼容浏览器时才运行 `npx playwright install chromium`。

详细步骤见 [前端说明](../frontend/README.md)。检查服务从空白隔离数据库启动，不复制正式数据库。真实测试输入从 KSP 游戏目录按需读取。

## 选型依据

参考 [Vite 环境要求](https://vite.dev/guide/)、[TanStack Query 重试机制](https://tanstack.com/query/latest/docs/framework/react/guides/query-retries) 和 [Flask 的 Waitress 部署说明](https://flask.palletsprojects.com/en/stable/deploying/waitress/)。依赖版本锁定为本项目实际构建验证过的版本；不把追逐最新主版本当成重建目标。

本次构建与回归结果见 [基座验证记录](validation/FOUNDATION_20261021.md)。

数据策略已更新为 [清零与游戏源测试](DATA_POLICY.md)，旧验证结果属于清零前记录。
