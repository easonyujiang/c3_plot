# React 工作台

React 19、TypeScript、Vite、TanStack Query，构建后由 Flask 提供。仅保留这一套界面，首页和 `/workspace/` 是同一入口。

```powershell
cd frontend
npm ci
npm run build
```

`dist/` 是日常启动所需的交付产物，应与源码同步更新并提交。开发时可用 `npm run dev`，其 API 代理读取根目录 `.env` 的 `C3_MASS_PORT`，默认 8765。也可在项目根目录用 `.venv/bin/python scripts/dev.py` 同时启动 API 和 Vite。

路由：`#/dashboard` 概览，`#/dictionary/<slug>` 火箭档案，`#/tools/mission` 轨道筛选，`#/tools/import` 游戏导入，`#/edit/<slug>` 独立编辑。

浏览器检查集中在 `tests/workspace.cjs`，覆盖三个屏幕尺寸、图表悬停/缩放/平移/恢复、整流罩切换、取消编辑不写入及任务查询。先从项目根目录启动隔离服务：

```powershell
.\.venv\Scripts\python.exe -m tools.dev.serve_validation --port 8878
```

在另一个终端运行：

```powershell
cd frontend
# 仅在没有可复用的兼容浏览器时安装：
# npx playwright install chromium
npm run check:ui
```

可用环境变量 `C3_TEST_URL`、`C3_BROWSER_OUTPUT` 设置服务与输出目录，`C3_BROWSER_CHANNEL=msedge` 使用已安装的 Edge。默认检查空库首页、从 KSP 存档按需预览并取消、空库任务查询；不预置历史火箭。设置 `C3_TEST_ROCKET` 可对之后由用户建立且具有实测标定的档案运行曲线交互检查。截图默认只放在临时目录并在结束时清除，显式设置 `C3_BROWSER_OUTPUT` 才保留。

统一视觉变量在 `src/tokens.css`；API 错误处理在 `src/api.ts`；缓存与重试策略在 `src/query.ts`。完整架构见 [技术栈说明](../docs/TECH_STACK.md)。
