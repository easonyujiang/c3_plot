import { useEffect, useState } from 'react';
import { api } from './api';
import type { Craft, Rocket, RocketConfig, CurveData } from './types';
import { CapacityChart } from './CapacityChart';
import { RocketEditor } from './RocketEditor';
import { MissionPanel } from './MissionPanel';
import { RocketDetails } from './RocketDetails';
function navigate(path:string){location.hash=path;}
function route(){try{return decodeURIComponent(location.hash.slice(1))||'/dashboard';}catch{return '/not-found';}}
const fmt=(n:number)=>Number.isFinite(n)?n.toFixed(3):'—';
export function App(){
  const [path,setPath]=useState(route);
  const [crafts,setCrafts]=useState<Craft[]>([]),[rockets,setRockets]=useState<Rocket[]>([]);
  const [config,setConfig]=useState<RocketConfig|null>(null),[data,setData]=useState<CurveData|null>(null);
  const [draft,setDraft]=useState<RocketConfig|null>(null),[search,setSearch]=useState('');
  const [busy,setBusy]=useState(true),[error,setError]=useState('');
  const [fairingOn,setFairingOn]=useState(false),[fairing,setFairing]=useState('0.2');
  const [,section='dashboard',slug]=path.split('/');
  const allowance=fairingOn?Number(fairing):0;
  const validFairing=Number.isFinite(allowance)&&allowance>=0&&(!fairingOn||fairing.trim()!=='');
  async function refresh(){
    const [library,list]=await Promise.all([api<{items:Craft[];warnings:string[]}>('/api/import/library'),api<Rocket[]>('/api/rockets')]);
    setCrafts(library.items);setRockets(list);
  }
  useEffect(()=>{
    function changed(){setPath(route());setError('');}
    addEventListener('hashchange',changed);
    void refresh().catch(e=>setError(String(e))).finally(()=>setBusy(false));
    const timer=setInterval(()=>void refresh().catch(()=>{}),10000);
    return()=>{removeEventListener('hashchange',changed);clearInterval(timer);};
  },[]);
  useEffect(()=>{
    if(!slug||slug==='new'||!['dictionary','edit'].includes(section)){
      setBusy(false);
      return;
    }
    let active=true;setBusy(true);setConfig(null);setData(null);
    Promise.all([api<RocketConfig>(`/api/rockets/${encodeURIComponent(slug)}`),api<CurveData>(`/api/data/${encodeURIComponent(slug)}`)])
      .then(([c,d])=>{if(active){setConfig(c);setData(d);}})
      .catch(e=>{if(active)setError(String(e));}).finally(()=>{if(active)setBusy(false);});
    return()=>{active=false;};
  },[section,slug]);
  async function importCraft(c:Craft){
    setBusy(true);setError('');
    try{
      const result=await api<{draft:RocketConfig;calibration_templates:RocketConfig['calibration']}>('/api/import/library/preview',{id:c.id,sha256:c.sha256});
      setDraft({...result.draft,calibration:result.calibration_templates});
      navigate('/edit/new');
    }catch(e){setError(String(e));}finally{setBusy(false);}
  }
  function fairingControl(){return <div className="fairing-controls"><label className="check"><input type="checkbox" checked={fairingOn} onChange={e=>setFairingOn(e.target.checked)}/>扣减整流罩</label>{fairingOn&&<label className="allowance">整流罩质量 / t<input type="number" min="0" step="0.01" value={fairing} onChange={e=>setFairing(e.target.value)}/></label>}<p className="muted">只扣减可用载荷，不计算抛弃过程；已计入基准点时请勿重复扣减。</p>{!validFairing&&<p role="alert" className="error">请输入非负有限质量。</p>}</div>;}
  function heading(title:string,description:string){return <div className="page-heading"><div><p className="eyebrow">C3 · 火箭运力</p><h1>{title}</h1><p className="muted">{description}</p></div><span className="badge">本地工作台</span></div>;}
  return <div className="shell">
    <header><a className="brand" href="#/dashboard">C3<span>火箭运力工作台</span></a><nav aria-label="主导航">{[['dashboard','概览'],['dictionary','火箭档案'],['tools','工具箱']].map(([key,label])=><a key={key} className={section===key?'active':''} aria-current={section===key?'page':undefined} href={`#/${key}`}>{label}</a>)}</nav></header>
    <main className={section==='dictionary'?'dictionary-layout':'single-layout'}>
      {section==='dictionary'&&<aside><div className="section-heading"><h2>火箭档案</h2><button onClick={()=>void refresh().catch(e=>setError(String(e)))}>刷新</button></div><label className="search"><span className="sr-only">搜索火箭</span><input placeholder="搜索火箭" value={search} onChange={e=>setSearch(e.target.value)}/></label><h3>已建立曲线 · {rockets.length}</h3>{rockets.filter(r=>r.name.toLowerCase().includes(search.toLowerCase())).map(r=><button className={`craft ${slug===r.slug?'selected':''}`} key={r.slug} onClick={()=>navigate(`/dictionary/${r.slug}`)}><strong>{r.name}</strong><small>查看数据与曲线 →</small></button>)}</aside>}
      <section className="workspace">
        {error&&<p role="alert" className="alert error">{error}</p>}{busy&&<p role="status">正在读取…</p>}
        {section==='dashboard'&&<>
          {heading('运力概览','火箭库、计算结果与任务入口。')}
          <div className="dashboard-stats"><div className="card"><small>火箭档案</small><strong>{rockets.length}</strong><a href="#/dictionary">查看所有数据 →</a></div><div className="card"><small>游戏火箭文件</small><strong>{crafts.length}</strong><a href="#/tools/import">导入新火箭 →</a></div><div className="card"><small>任务示例</small><strong>700 kg</strong><a href="#/tools/mission">400 × 40,000 km · 89° →</a></div></div>
          <div className="card"><div className="section-heading"><h2>火箭数据与曲线</h2><a href="#/dictionary">打开火箭档案 →</a></div>{rockets.map(r=><a className="recent-row" key={r.slug} href={`#/dictionary/${r.slug}`}><strong>{r.name}</strong><span>浏览档案 →</span></a>)}</div>
          <div className="tool-cards"><a className="card" href="#/tools/mission"><h2>按任务筛选火箭</h2><p className="muted">自定义载荷、近远地点与倾角，检查任务点火步骤。</p></a><a className="card" href="#/tools/import"><h2>从游戏导入</h2><p className="muted">直接选择已保存的 craft，进入独立导入与修改界面。</p></a></div>
        </>}
        {section==='dictionary'&&<>
          {heading(config?.rocket.name||'火箭档案','集中浏览火箭数据、基准点与曲线。修改请进入独立界面。')}
          {!slug?<div className="empty"><h2>选择一份火箭档案</h2><p>左侧列出已保存的数据。这里用于查阅，不会直接修改火箭。</p></div>:config&&<>
            <RocketDetails config={config} slug={slug}/>
            <div className="card"><h2>基准点记录</h2><div className="table-scroll"><table><thead><tr><th>基准点</th><th>载荷 / t</th><th>轨道高度 / km</th><th>剩余 Δv / m/s</th></tr></thead><tbody>{config.calibration.map((p,i)=><tr key={i}><td>{i+1}</td><td>{fmt(p.payload_t)}</td><td>{p.orbit_alt_km}</td><td>{p.dv_rem_m_s}</td></tr>)}</tbody></table></div></div>
            <div className="card"><h2>运力曲线</h2>{fairingControl()}{data&&validFairing&&<><CapacityChart data={data} fairing={allowance}/><div className="legend"><span>● 基准载荷范围内</span><span>┄ 范围外推算</span></div><p className="muted">220 km 参考轨道 · 参考倾角 19.81° · 其他轨道使用工具箱中的等效 C3 对照</p></>}</div>
          </>}
        </>}
        {section==='tools'&&<>
          {heading('工具箱','导入火箭、筛选运力，按用途打开工具。')}
          <nav className="tool-nav" aria-label="工具导航"><a href="#/tools/mission">轨道与运力筛选</a><a href="#/tools/import">游戏火箭导入</a></nav>
          {(slug==='mission'||!slug)&&<><div className="card"><h2>载荷扣减选项</h2>{fairingControl()}</div><MissionPanel allowance={allowance} validFairing={validFairing}/></>}
          {slug==='import'&&<div className="card"><div className="section-heading"><h2>游戏中的火箭 · {crafts.length}</h2><button onClick={()=>void refresh().catch(e=>setError(String(e)))} disabled={busy}>刷新</button></div><label className="search"><span className="sr-only">搜索火箭或存档</span><input placeholder="搜索火箭或存档" value={search} onChange={e=>setSearch(e.target.value)}/></label><p className="muted">每 10 秒读取已保存的 VAB 火箭。点击导入后进入独立修改界面。</p><div className="import-grid">{crafts.filter(c=>`${c.name} ${c.save} ${c.filename}`.toLowerCase().includes(search.toLowerCase())).map(c=><button className="craft" key={c.id} disabled={busy} onClick={()=>void importCraft(c)}><strong>{c.name||c.filename}</strong><small>{c.save} · {c.filename} · 点击导入</small></button>)}</div></div>}
        </>}
        {section==='edit'&&((slug==='new'?draft:config)?<RocketEditor key={slug} initial={(slug==='new'?draft:config)!} slug={slug==='new'?null:slug||null} onCancel={()=>navigate(slug==='new'?'/tools/import':`/dictionary/${slug}`)} onSaved={(savedSlug,result)=>{setData(result);void refresh();navigate(`/dictionary/${savedSlug}`);}}/>:!busy&&<p>无法读取编辑对象，请返回火箭库选择。</p>)}
        {!['dashboard','dictionary','tools','edit'].includes(section)&&<p>页面不存在。<a href="#/dashboard">返回首页</a></p>}
      </section>
    </main>
  </div>;
}
