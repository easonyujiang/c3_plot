# KSP 受控质量实验——人工采集步骤

实验目录：experiments\control_mass

## 为什么需要这个实验

现有 20 个 VAB 样本中 parachuteSingle 与 mk1pod.v2 的计数完全共线，
无法把 +0.075 t 残差归因到两者之一。本实验用最小载具逐一改变降落伞
数量与直径，用配对差分打破共线性。

## 绑定的控制核心（唯一，不得更换）

- 部件：probeCoreOcto2_v2
- 标题：探测动力-OKTO2
- 补丁后质量：0.04 t
- 类别：Pods
- attachRules：1,0,1,1,0
- stack 节点：['node_stack_bottom', 'node_stack_top']
- 选择理由：probeCoreOcto2_v2（Pods 类，质量 0.04 t）：attachRules=1,0,1,1,0（allowSrfAttach=True，allowStack=True）；控制核心自身具有 2 个独立 stack 节点 ['node_stack_bottom', 'node_stack_top']；当前 parachuteSingle 组合仅使用 stack 路径，M1/M3 用 node_stack_top，M2 分别用 node_stack_top 与 node_stack_bottom
- 配置摘要 SHA-256：e3aa21ef8a0235f8e181f32f37209b8b7528bfb26cb4a31219bedf24f590e8b0

四份 craft 的根部件都必须等于这个控制核心；更换核心会被 analyze 判为 invalid_design。

## 第 0 步：准备独立存档

1. 在 KSP 主菜单新建一个独立存档（或在独立复制的 KSP 安装中进行），
   四份 craft 必须在同一个存档中手动搭建；
2. 确保该存档使用与 prepare 相同的模组与 ModuleManager 配置。

## 第 1 步：M0_control

1. 在 VAB 中把绑定的控制核心作为根部件放下；
2. 不添加任何其他部件；推进剂与可变资源为空（或在四份 craft 中完全一致）；
3. 保存为 M0_control。

## 第 2 步：M1_one_chute_default

1. 加载 M0_control，把 parachuteSingle 安装到控制核心的 node_stack_top 节点（当前组合为 stack 路径，径向路径不可用）；
2. 关闭对称（单一实例）；不修改任何 RealChute 参数（保持默认）；
3. 保存 → 退出 VAB → 重新加载该 craft → 再保存一次，文件名为 M1_one_chute_default；
4. 不得修改根部件、资源或其他部件。

## 第 3 步：M2_two_chutes_default

1. 加载 M0_control，添加两个 parachuteSingle；
2. 不使用对称复制：逐个放置两个独立实例，参数一致（均默认）；
3. 一个安装到 node_stack_top，另一个安装到 node_stack_bottom（两个独立节点，各挂一个；禁止把第二个挂到第一个降落伞上）；
4. 保存为 M2_two_chutes_default。

## 第 4 步：M3_one_chute_changed_diameter

1. 复制 M1（加载 M1 后立即另存为 M3_one_chute_changed_diameter）；
2. 在 RealChute UI 中把展开直径改为与 M1 明显不同的值（建议 15.0–26.0 m，避开 20.7 ± 0.5 m）；
3. 不得修改 caseMass、材料、根部件、资源或其他参数；
4. 保存后打开 craft 文件，确认 ProceduralChute 的 depDiam 已更新。

## 第 5 步：找到 KSP 生成的 craft 与 loadmeta

KSP 把 craft 与 loadmeta 保存在存档目录：

    <KSP>/saves/<存档名>/Ships/VAB/

每个 craft 对应一个 .craft 与同名 .loadmeta（loadmeta 在保存/飞行时由 KSP 生成）。

## 第 6 步：复制到实验输入目录

把下列 8 个文件复制到 input/ 目录（本仓库内）：

- M0_control.craft 与 M0_control.loadmeta
- M1_one_chute_default.craft 与 M1_one_chute_default.loadmeta
- M2_two_chutes_default.craft 与 M2_two_chutes_default.loadmeta
- M3_one_chute_changed_diameter.craft 与 M3_one_chute_changed_diameter.loadmeta

## 第 7 步：运行分析

    C:\ProgramData\anaconda3\python.exe ksp_mass_control_experiment.py analyze --input <实验目录> --json

只有四组 craft/loadmeta 全部到位后才会分析；任何校验失败都会以 invalid_design 列出具体差异（机器可读 code），不会给出判定。

## 数值精度模型（动态 binary32，prepare 时记录）

真实 KSP .loadmeta 的 totalMass 小数位数在 5–9 位之间分布，固定七位小数假设不成立。误差界限按 IEEE-754 binary32 往返序列化动态计算：

- 序列化模型：ieee754_binary32_round_trip
- 预测端相对误差因子：4.76837158203125e-07
- 实验裕量：1e-07 t
- M3 直径最小变化：2.0 m

每条差分/线性/直径判定使用动态观测误差界限 + 预测误差 + 实验裕量；观测到的质量邻值与 ULP 写入分析报告。

## 重新采集

若 analyze 报 invalid_design（例如某份 craft 意外多了一个部件、M3 改了其他参数、或指纹与 prepare 不一致），请删除 input/ 中的对应文件，回到对应步骤重新搭建并重新复制，然后重新运行 analyze。只要 input/ 中还有任何文件，prepare 就拒绝覆盖 manifest。

## 当前状态

**awaiting_ksp_capture**——本阶段不运行 KSP、不伪造任何 craft/loadmeta；所有结论必须来自用户在 KSP 中生成的真实数据。
