# 前端工作台

React + TypeScript + Vite。构建后的页面由现有 Flask 服务提供，无需单独启动 Node 服务。

```powershell
cd frontend
npm ci
npm run build
```

启动项目后首页就是新版工作台；`/workspace/` 同样可用，原界面保留在 `/legacy/`。

- `#/dashboard`：概览、火箭库入口和任务入口。
- `#/dictionary/<slug>`：只读火箭数据、基准点和曲线。
- `#/tools/mission`：自定义轨道与载荷筛选。
- `#/tools/import`：发现并导入游戏火箭。
- `#/edit/<slug>`：独立修改；取消不写入，保存后返回档案。

整流罩开关是显示与任务筛选的额外载荷扣减，不修改基准点，不模拟抛弃。
曲线支持滚轮缩放、框选、平移、悬停读数及双击恢复。工具箱同时显示目标轨道 C3 和含倾角速度修正的等效 C3。
真实编辑器保存、自动识别、有限点火次数、来源持久化和完整浏览器流程已验收。详见 [工作流验收](../docs/WORKFLOW_ACCEPTANCE_20261011.md)。
