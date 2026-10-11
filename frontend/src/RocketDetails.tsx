import type { RocketConfig } from './types';
export function RocketDetails({config,slug}:{config:RocketConfig;slug:string}){
  const automatic=(config.automatic_identification as {status:string}|undefined)?.status==='verified_editor_snapshot';
  return <div className="card"><div className="section-heading"><h2>火箭信息</h2><a className="button primary" href={`#/edit/${slug}`}>修改火箭数据</a></div>
    <div className="stages">{config.stages.map((s,i)=><div key={i}><small>第 {i+1} 级</small><strong>{s.wet_mass_t.toFixed(3)} t</strong><p>干质量 {s.dry_mass_t.toFixed(3)} t</p>
      <p>多次点火：{automatic?(s.max_ignitions==null?'未设次数上限':`最多 ${s.max_ignitions} 次`):s.multi_ignition?'已配置，次数待核对':'未配置'}</p>
      {s.runtime_performance&&<><p>总推力 {s.runtime_performance.thrust.toFixed(1)} kN</p><p>真空比冲 {s.runtime_performance.isp_vac.toFixed(1)} s</p></>}
      {s.tank_states?.filter(t=>t.module==='ModuleFuelTanks').map(t=><div key={t.part_id}><p>油箱类型：{t.fields.type}</p><p>可用容积 {Number(t.fields.volume).toLocaleString()} L</p><p>几何容积 {Number(t.fields.totalVolume).toLocaleString()} L · 利用率 {t.fields.utilization}%</p></div>)}
    </div>)}</div>
    <p className="muted">{automatic?'来自当前游戏编辑器结算记录；发动机集群总推力已包含集群数量，油箱质量已包含 Mod 修正。干质量还包含非推进剂资源与无法按比例消耗的余量。':'历史质量与基准点来源仍需核对；在游戏编辑器保存后重新导入可采集当前 Mod 数据。'}</p>
    {automatic&&<details><summary>查看质量与分级依据</summary><p>按实际分离连接划分物理级，排除发射夹；按照资源密度、可达油箱与燃料比例结算可用推进剂。软件保存来源指纹，游戏配置变化后需要重新采集。</p></details>}
  </div>;
}
