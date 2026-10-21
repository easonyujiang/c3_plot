# React 工作台

React、TypeScript、Vite，构建后由 Flask 提供。仅保留这一套界面，首页和 `/workspace/` 是同一入口。

```powershell
cd frontend
npm ci
npm run build
```

`dist/` 是日常启动所需的交付产物，应与源码同步更新并提交。开发时可用 `npm run dev`，其 API 代理指向本地 8878 端口。

路由：`#/dashboard` 概览，`#/dictionary/<slug>` 火箭档案，`#/tools/mission` 轨道筛选，`#/tools/import` 游戏导入，`#/edit/<slug>` 独立编辑。

浏览器检查集中在 `tests/workspace.cjs`，覆盖三个屏幕尺寸、图表悬停/缩放/平移/恢复、整流罩切换、取消编辑不写入及任务查询。先从项目根目录启动隔离服务：

```powershell
.\.venv\Scripts\python.exe -m tools.dev.serve_validation --output output/ui-check --port 8878
```

在另一个终端运行：

```powershell
cd frontend
npx playwright install chromium
npm run check:ui
```

可用环境变量 `C3_TEST_URL`、`C3_BROWSER_OUTPUT` 设置服务与输出目录，`C3_BROWSER_CHANNEL=msedge` 使用已安装的 Edge。检查使用 Neutron 历史样例与模型数值，不能代替实际飞行验证。隔离服务每次使用新的输出目录，避免覆盖之前证据。
