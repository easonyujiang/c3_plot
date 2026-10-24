# 上升仿真内核 T0/T1（Phase B 初版）

更新：2026-10-24。对应 [产品路线图](../PRODUCT_ROADMAP.md) 的 Phase B。当前是纯 Python 骨架，不接 KSP、不写数据库、不声称飞行精度。

## T0：理想火箭方程

`total_ideal_dv(stages, payload_t)` 按级段从下到上累加：

```text
Δv = Σ Isp_i * g0 * ln((wet_i + mass_above) / (dry_i + mass_above))
```

用于快速上限/初值，不包含重力损失、阻力、转向和有限燃烧。

## T1：平面 3DOF 上升

`simulate_ascent(stages, payload_t, planet=..., pitch=..., settings=...)`

状态：

```text
(radius_m, downrange_rad, radial_velocity_m_s, tangential_velocity_m_s, mass_t)
```

模型：

- 球形引力 `-mu/r²`；
- 指数大气 `rho = rho0 * exp(-h/H)`，超过大气高度为 0；
- 阻力 `0.5 * rho * v² * Cd * A`，方向与速度相反；
- 推力/Isp 换算质量流；级段按列表顺序燃烧，燃尽后抛掉该级干质量并立即点火下一级；
- 简单俯仰程序：低于 `vertical_altitude_m` 保持最大俯仰，之后按 `turn_rate_deg_per_km` 下降；
- 可选初始切向速度（发射场地球自转的一阶近似）；
- 固定步长 RK4；在级段燃尽时把步长截到恰好耗尽，避免负推进剂。

输出：

- 1 步/采样的时间、高度、航程、径向/切向速度、速度、质量、动压、加速度、推力、级；
- 级段抛离事件（时间、高度、抛离前后质量）；
- 最大动压、最大加速度、初始/最终质量、停止原因。

## 用法

```python
from c3_mass.ascent_simulation import (
    AscentSettings, PitchProgram, Planet, StageSpec, simulate_ascent, total_ideal_dv)

stages = [
    StageSpec(dry_mass_t=6.0, propellant_t=126.0, thrust_kN=2400.0, isp_s=305.0),
    StageSpec(dry_mass_t=0.9, propellant_t=18.6, thrust_kN=213.0, isp_s=300.0),
]
result = simulate_ascent(stages, 5.0, planet=Planet(), pitch=PitchProgram(),
                         settings=AscentSettings(dt_s=0.05, max_time_s=600))
print(result.final.speed_m_s, result.max_dynamic_pressure_Pa,
      [event.to_dict() for event in result.stage_events])
```

## 当前边界

- **平面 2D**：没有 3D 方位/倾角、没有真实发射场几何；初始切向速度只是一阶近似；
- **没有轨道插入/制导**：在最后一级抛离、超时或坠毁时停止；不判断是否入轨；
- **大气/气动是代理**：指数大气 + 常数 Cd/A，不是 FAR/6DOF；
- **没有地球自转随高度的完整模型、没有风、没有热/结构/控制**；
- **没有用 KSP 实飞标定**：当前只做合成数据测试，不能当作已验证运力；
- 参数必须来自 KSP 源文件/编辑器快照/飞行实验；当前模块只消费调用方给的输入。

## 测试

`tests/test_ascent_simulation.py` 覆盖：

- 单级真空水平加速与火箭方程一致；
- 两级总 Δv 与质量抛离顺序；
- 阻力降低末速度并记录动压；
- 时间步收敛；
- 非法级段参数拒绝。

当前 6 项测试通过；完整回归待游戏结束后运行，避免在用户游戏时扫描/读取游戏目录。
