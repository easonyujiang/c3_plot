import { useState } from 'react';
import type { RocketConfig, CurveData } from './types';
import { api } from './api';

export function RocketEditor({initial,slug,onSaved,onCancel}:{initial:RocketConfig;slug:string|null;onSaved:(slug:string,data:CurveData)=>void;onCancel:()=>void}) {
  const [draft,setDraft]=useState(()=>structuredClone(initial));
  const [busy,setBusy]=useState(false),[error,setError]=useState('');
  const identification=draft.automatic_identification as {status:string;message?:string}|undefined;
  const automatic=identification?.status==='verified_editor_snapshot';
  const [advanced,setAdvanced]=useState(!draft.calibration.every(p=>p.benchmark));
  const ready=draft.rocket.name.trim().length>0&&draft.stages.length>0&&
    draft.stages.every(s=>s.wet_mass_t>s.dry_mass_t&&s.dry_mass_t>0)&&
    draft.calibration.length>=3&&new Set(draft.calibration.map(p=>p.payload_t)).size>=3&&
    draft.calibration.every(p=>p.payload_t>0&&p.orbit_alt_km>0&&p.dv_rem_m_s>=0&&[p.payload_t,p.orbit_alt_km,p.dv_rem_m_s].every(Number.isFinite));
  function number(value:string){return value===''?NaN:Number(value);}
  async function save(){
    setBusy(true);setError('');
    try{
      const result=await api<{slug:string;result:CurveData}>(slug?`/api/rockets/${slug}`:'/api/rockets',draft,slug?'PUT':'POST');
      onSaved(result.slug,result.result);
    }catch(e){setError(e instanceof Error?e.message:String(e));}finally{setBusy(false);}
  }
  return <div className="editor" aria-label="火箭修改界面">
    <div className="page-heading"><div><p className="eyebrow">独立编辑</p><h1>{slug?'修改火箭':'导入火箭'}</h1><p className="muted">保存后更新数据与曲线；取消不会写入修改。</p></div><button onClick={onCancel} disabled={busy}>取消并返回</button></div>
    {error&&<p role="alert" className="alert error">{error}</p>}
    <div className="card"><h2>火箭信息与分级</h2><label>火箭名称<input value={draft.rocket.name} onChange={e=>setDraft({...draft,rocket:{...draft.rocket,name:e.target.value}})}/></label>
      <p className="muted">{automatic?'已与游戏编辑器结算质量、当前 Mod 和火箭版本核对。分级与发动机数据自动填写。':identification?.message||'动态质量尚未自动确认，请在游戏编辑器保存火箭后重新导入。'}</p>
      {draft.stages.map((s,i)=><fieldset className="edit-stage" key={i}><legend>第 {i+1} 级</legend><div className="mission-fields">
        {([['wet_mass_t','湿质量 / t'],['dry_mass_t','干质量 / t']] as const).map(([key,label])=><label key={key}>{label}<input type="number" readOnly={automatic} min="0" step="any" value={Number.isFinite(s[key])?s[key]:''} onChange={e=>setDraft({...draft,stages:draft.stages.map((p,j)=>j===i?{...p,[key]:number(e.target.value)}:p)})}/></label>)}
        <label className="check"><input type="checkbox" disabled={automatic} checked={s.multi_ignition} onChange={e=>setDraft({...draft,stages:draft.stages.map((p,j)=>j===i?{...p,multi_ignition:e.target.checked}:p)})}/>支持多次点火 {automatic?(s.max_ignitions==null?'（未设次数上限）':`（最多 ${s.max_ignitions} 次）`):''}</label>
      </div></fieldset>)}
    </div>
    <div className="card"><h2>三个基准点</h2><p className="muted">默认只填写三个任务的实测运力。这里的载荷是同一箭体模板额外承载的质量；模板中固定设备已算入箭体，不要重复填写。</p><label className="check"><input type="checkbox" checked={advanced} onChange={e=>setAdvanced(e.target.checked)}/>自定义基准轨道与剩余 Δv</label>
      <div className="baseline-grid">{draft.calibration.slice(0,3).map((p,i)=><fieldset key={i}><legend>{p.benchmark||`基准 ${i+1}`}</legend>
        {([['payload_t','实测运力 / t'],['orbit_alt_km','圆轨道高度 / km'],['dv_rem_m_s','剩余 Δv / m·s⁻¹']] as const).filter(([key])=>advanced||key==='payload_t').map(([key,label])=><label key={key}>{label}<input type="number" min="0" step="any" value={Number.isFinite(p[key])?p[key]:''} onChange={e=>setDraft({...draft,calibration:draft.calibration.map((point,j)=>j===i?{...point,[key]:number(e.target.value)}:point)})}/></label>)}
      </fieldset>)}</div>
      <div className="actions"><span className="muted">{ready?'可以保存并重新计算':'质量尚未确认或基准点不完整'}</span><button className="primary" disabled={!ready||busy} onClick={()=>void save()}>{busy?'正在保存…':'保存并绘制曲线'}</button></div>
    </div>
  </div>;
}
