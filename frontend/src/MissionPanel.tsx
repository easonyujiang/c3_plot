import { useState } from 'react';

type Candidate = {
  slug: string; rocket: string; capacity: number; verified_capacity: number|null;
  verification_status: string; recommendation_eligible: boolean;
  verified_feasible: boolean|null; detailed_reason: string; reason: string;
  dv_feasible: boolean; supports_restart: boolean;
  detailed_validation: {calibration_support?:{status:string;measured_payload_min_t:number;measured_payload_max_t:number};burn_timeline: {name:string;target_dv_m_s:number;ignition_kind:string|null;stage_index:number|null}[]};
};
type Response = {
  mission: {periapsis_km:number;apoapsis_km:number;inclination_deg:number;required_dv:number;post_parking_ignitions:number;c3:number;c3_effective:number;dv_launch_plane:number;dv_departure:number;dv_arrival:number;delta_i_deg:number;launch_lat:number};
  results: Candidate[];
};
export function MissionPanel({allowance,validFairing}:{allowance:number;validFairing:boolean}) {
  const [periapsis,setPeriapsis]=useState('400');
  const [apoapsis,setApoapsis]=useState('40000');
  const [inclination,setInclination]=useState('89');
  const [payload,setPayload]=useState('700');
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const [response,setResponse]=useState<Response|null>(null);
  const [queriedAllowance,setQueriedAllowance]=useState(0);
  async function query() {
    setBusy(true);setError('');setResponse(null);
    try {
      const result=await fetch('/api/mission-query',{
        method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({orbit_type:'elliptical',periapsis_km:Number(periapsis),apoapsis_km:Number(apoapsis),
          inclination_deg:Number(inclination),payload_t:Number(payload)/1000+allowance,
          parking_alt_km:220,launch_lat:19.81,arrival_strategy:'combined'}),
      });
      const data=await result.json();
      if(!result.ok) throw new Error(data.error||'查询失败');
      setResponse(data);setQueriedAllowance(allowance);
    }catch(e){setError(e instanceof Error?e.message:String(e));}finally{setBusy(false);}
  }
  const stale=queriedAllowance!==allowance;
  function update(set:(v:string)=>void,value:string){set(value);setResponse(null);}
  return <div className="card mission-panel">
    <div className="section-heading"><h2>按任务筛选火箭</h2><span className="badge">自定义地球轨道</span></div>
    <p className="muted">220 km 停泊轨道 · 发射纬度 19.81° · 基准向东发射</p>
    <form onSubmit={e=>{e.preventDefault();void query();}}>
      <div className="mission-fields">
        <label>卫星质量 / kg<input type="number" min="1" required value={payload} onChange={e=>update(setPayload,e.target.value)}/></label>
        <label>近地点 / km<input type="number" min="80" required value={periapsis} onChange={e=>update(setPeriapsis,e.target.value)}/></label>
        <label>远地点 / km<input type="number" min="220" required value={apoapsis} onChange={e=>update(setApoapsis,e.target.value)}/></label>
        <label>轨道倾角 / °<input type="number" min="0" max="180" step="any" required value={inclination} onChange={e=>update(setInclination,e.target.value)}/></label>
      </div>
      <div className="actions"><span className="muted">{allowance>0?`额外扣减整流罩 ${(allowance*1000).toFixed(0)} kg`:'不额外扣减整流罩'}</span><button className="primary" disabled={busy||!validFairing}>{busy?'正在筛选…':'筛选火箭'}</button></div>
    </form>
    {error&&<p role="alert" className="alert error">{error}</p>}
    {response&&!stale&&<div className="mission-results" role="status">
      <p className="query-result">{response.mission.periapsis_km} × {response.mission.apoapsis_km} km · {response.mission.inclination_deg}°<br/><small>停泊后 {response.mission.post_parking_ignitions} 次点火；额外速度预算 {response.mission.required_dv.toFixed(1)} m/s</small></p>
      <div className="mission-energy"><span>目标轨道 C3 <strong>{response.mission.c3.toFixed(3)}</strong></span><span title="将包含倾角修正的总速度需求折算为 220 km 参考轨道的 C3，用于对照运力曲线">等效 C3 <strong>{response.mission.c3_effective.toFixed(3)}</strong></span><small>km²/s²</small></div>
      <details className="mission-assumptions"><summary>速度预算</summary><p>出发点火 {response.mission.dv_departure.toFixed(1)} m/s · 远地点点火 {response.mission.dv_arrival.toFixed(1)} m/s · 发射方向修正 {response.mission.dv_launch_plane.toFixed(1)} m/s</p><p>轨道平面变化 {response.mission.delta_i_deg.toFixed(2)}° · 参考倾角 19.81°；等效 C3 为运力对照坐标。</p></details>
      {response.results.length===0&&<p>火箭库中尚无可筛选数据。</p>}
      {response.results.map(r=>{
        const capacity=(r.verified_capacity??r.capacity)-queriedAllowance;
        const candidate=r.verified_feasible===true;
        return <article className="candidate" key={r.slug}>
          <div className="section-heading"><strong>{r.rocket}</strong><span className={`badge ${r.recommendation_eligible?'':'warning'}`}>{r.recommendation_eligible?'模型校验通过':candidate?r.detailed_validation.calibration_support?.status==='extrapolated'?'外推估计':'运力估计':'未满足／数据不足'}</span></div>
          <p>可用载荷估计 <strong>{Math.max(0,capacity*1000).toFixed(0)} kg</strong> · 载荷余量 <strong>{(capacity*1000-Number(payload)).toFixed(0)} kg</strong></p>
          <p className="muted">{r.detailed_reason||r.reason}</p>
          <details><summary>查看任务点火步骤</summary>{r.detailed_validation.burn_timeline.map((b,i)=><p key={i}>{i+1}. {b.name} · {b.target_dv_m_s.toFixed(1)} m/s · {b.stage_index===null?'执行级待确认':`第 ${b.stage_index+1} 级`} {b.ignition_kind==='restart'?'再次点火':''}</p>)}</details>
        </article>;
      })}
    </div>}
    {response&&stale&&<p className="muted">整流罩扣减已变化，请重新筛选。</p>}
  </div>;
}
