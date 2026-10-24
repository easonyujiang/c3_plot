# Campaign 与试验队列契约（Phase C 初版）

更新：2026-10-24。对应 [产品路线图](../PRODUCT_ROADMAP.md) 的 Phase C。当前只实现**确定性数据契约与状态机**，不启动 KSP、不读游戏目录。

## 结构

| 结构 | 作用 |
| --- | --- |
| `CampaignSpec` | 火箭 profile、craft 哈希、显式 trial 列表、程序 recipe、容差、`max_trials`（默认/硬上限 6） |
| `TrialSpec` | 沿用 Phase A：载荷、目标轨道、程序、容差、预算 |
| `TrialRecord` | 单个 trial 的 id、状态、尝试次数、`TrialResult` |
| `CampaignQueue` | 确定性队列；同一时刻只允许一个 running；状态迁移与统计 |

状态：

```text
queued → running → completed | failed
queued/running → cancelled
```

禁止：未 queued 直接 finish、同时启动两个 trial、对已完成 trial 再 cancel。

## 使用

```python
from server.campaign import CampaignSpec, CampaignQueue

spec = CampaignSpec(
    rocket_profile_id=profile['profile_id'],
    rocket_sha256=profile['source']['craft_sha256'],
    max_trials=6,
    trials=[
        {'payload_t': 5.0, 'orbit': {'periapsis_km': 220, 'apoapsis_km': 220,
                                     'inclination_deg': 20}},
        {'payload_t': 2.5, 'orbit': {'periapsis_km': 220, 'apoapsis_km': 220,
                                     'inclination_deg': 20}},
        # ...
    ],
    program={'staging': 'user-provided', 'pvq': '...'},
    tolerances={'orbit_km': 5, 'hold_s': 30},
)
queue = CampaignQueue(spec)
trial = queue.next_queued()
queue.start(trial.trial_id)
# 真实执行器跑完后：
queue.finish(trial.trial_id, TrialResult(spec_id=trial.trial_id,
                                          status='verified',
                                          remaining_dv_m_s=1200))
print(queue.snapshot())
```

## 设计原则

- **确定性**：trial id 为 `<campaign_id>-0001`，`campaign_id` 由 profile/craft/trials/program/tolerances/max_trials 稳定哈希；相同 spec 得到相同 id。
- **显式列表**：不自动生成轨道×载荷笛卡尔积；6 架次测试计划由用户/规划器显式给出。
- **失败不是重飞**：`fail()` 记录 `failure_code`，不自动重试；后续编排器按信息量决定是否用剩余 trial。
- **纯状态**：没有线程、文件、KSP、Flask、SQLite；便于单元测试和 dry-run。
- **硬上限**：`max_trials <= 6` 由调用方/后续 campaign 规划器保证；当前契约只拒绝 `len(trials) > max_trials`。

## 测试

`tests/test_campaign.py` 覆盖：

- 确定性 campaign id；
- 队列顺序与单 running；
- completed / failed / cancelled 状态迁移；
- 非法 spec/状态迁移拒绝；
- snapshot JSON 序列化。

下一步：把 `CampaignQueue` 接到真实执行器（prepare → KSP → audit）和 Phase B 仿真预飞；当前没有自动启动游戏。
