import { lazy, Suspense, useEffect, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Activity, ArrowUpRight, BookOpen, CircleHelp, LayoutDashboard, Orbit, Plus, Rocket as RocketIcon, Wrench } from 'lucide-react';
import { api } from './api';
import type { Craft, Rocket, RocketConfig, CurveData } from './types';
const CapacityChart = lazy(() => import('./CapacityChart').then(module => ({ default: module.CapacityChart })));
const RocketEditor = lazy(() => import('./RocketEditor').then(module => ({ default: module.RocketEditor })));
const MissionPanel = lazy(() => import('./MissionPanel').then(module => ({ default: module.MissionPanel })));
import { RocketDetails } from './RocketDetails';
function navigate(path:string){location.hash=path;}
function route(){try{return decodeURIComponent(location.hash.slice(1))||'/dashboard';}catch{return '/not-found';}}
const fmt=(n:number)=>Number.isFinite(n)?n.toFixed(3):'—';
export function App(){
  const [path,setPath]=useState(route);
  const client=useQueryClient();
  const [draft,setDraft]=useState<RocketConfig|null>(null),[search,setSearch]=useState('');
  const [importing,setImporting]=useState(false),[error,setError]=useState('');
  const [fairingOn,setFairingOn]=useState(false),[fairing,setFairing]=useState('0.2');
  const [,section='dashboard',slug]=path.split('/');
  const library=useQuery({queryKey:['library'],queryFn:({signal})=>api<{items:Craft[];warnings:string[]}>('/api/import/library',undefined,'GET',signal),refetchInterval:10000});
  const fleet=useQuery({queryKey:['rockets'],queryFn:({signal})=>api<Rocket[]>('/api/rockets',undefined,'GET',signal)});
  const system=useQuery({queryKey:['system'],queryFn:({signal})=>api<{version:string;ksp:{connected:boolean;config_available:boolean;exporter_available:boolean}}>('/api/system',undefined,'GET',signal),refetchInterval:30000});
  const hasRocket=!!slug&&slug!=='new'&&['dictionary','edit'].includes(section);
  const rocket=useQuery({queryKey:['rocket',slug],enabled:hasRocket,queryFn:({signal})=>api<RocketConfig>(`/api/rockets/${encodeURIComponent(slug!)}`,undefined,'GET',signal)});
  const curve=useQuery({queryKey:['curve',slug],enabled:hasRocket,queryFn:({signal})=>api<CurveData>(`/api/data/${encodeURIComponent(slug!)}`,undefined,'GET',signal)});
  const crafts=library.data?.items??[],rockets=fleet.data??[];
  const config=hasRocket?rocket.data:null,data=hasRocket?curve.data:null;
  const busy=importing||fleet.isPending||library.isPending||(hasRocket&&(rocket.isPending||curve.isPending));
  const queryError=fleet.error||library.error||(hasRocket&&(rocket.error||curve.error));
  const allowance=fairingOn?Number(fairing):0;
  const validFairing=Number.isFinite(allowance)&&allowance>=0&&(!fairingOn||fairing.trim()!=='');
  async function refresh(){
    await Promise.all([client.invalidateQueries({queryKey:['library']}),client.invalidateQueries({queryKey:['rockets']})]);
  }
  useEffect(()=>{
    function changed(){setPath(route());setError('');setSearch('');window.scrollTo(0,0);}
    addEventListener('hashchange',changed);
    return()=>removeEventListener('hashchange',changed);
  },[]);
  async function importCraft(c:Craft){
    setImporting(true);setError('');
    try{
      const result=await api<{draft:RocketConfig;calibration_templates:RocketConfig['calibration']}>('/api/import/library/preview',{id:c.id,sha256:c.sha256});
      setDraft({...result.draft,calibration:result.calibration_templates});
      navigate('/edit/new');
    }catch(e){setError(String(e));}finally{setImporting(false);}
  }
  function fairingControl(){return <div className="fairing-controls"><label className="check"><input type="checkbox" checked={fairingOn} onChange={e=>setFairingOn(e.target.checked)}/>扣减整流罩</label>{fairingOn&&<label className="allowance">整流罩质量 / t<input type="number" min="0" step="0.01" value={fairing} onChange={e=>setFairing(e.target.value)}/></label>}<p className="muted">只扣减可用载荷，不计算抛弃过程；已计入基准点时请勿重复扣减。</p>{!validFairing&&<p role="alert" className="error">请输入非负有限质量。</p>}</div>;}
  function heading(title:string,description:string){return <div className="page-heading"><div><p className="eyebrow">C3 · 火箭运力</p><h1>{title}</h1><p className="muted">{description}</p></div><a className="button primary" href="#/tools/import"><Plus size={15}/> 导入火箭</a></div>;}
  return <div className="shell">
    <a className="skip-link" href="#main-content" onClick={event=>{event.preventDefault();document.getElementById('main-content')?.focus();}}>跳至内容</a>
    <header className="app-sidebar">
      <a className="brand" href="#/dashboard"><span className="brand-symbol"><Orbit size={25}/></span><span>C3 MASS<small>火箭运力工作台</small></span></a>
      <p className="nav-label">工作空间</p>
      <nav aria-label="主导航">{([['dashboard','概览',LayoutDashboard],['dictionary','火箭档案',BookOpen],['tools','工具箱',Wrench]] as const).map(([key,label,Icon])=><a key={key} className={section===key?'active':''} aria-current={section===key?'page':undefined} href={`#/${key}`}><Icon size={18}/><span>{label}</span><ArrowUpRight className="nav-arrow" size={14}/></a>)}</nav>
      <div className="sidebar-note"><Orbit size={32}/><strong>从地面，向深空。</strong><p>用实测建立基准，
让每一次任务有据可依。</p><a href="#/tools/mission">规划轨道任务 <ArrowUpRight size={14}/></a></div>
      <div className="sidebar-footer"><Activity size={15}/><span>{system.isError?'服务状态读取失败':system.data?'本地服务已连接':'正在连接服务'}<small>{system.data?`v${system.data.version}`:'C3-Mass'}</small></span><i className={system.isError?'offline':''}/></div>
    </header>
    <div className="app-content">
    <div className="topbar"><span>工作空间 <span className="separator">/</span> <strong>{({dashboard:'概览',dictionary:'火箭档案',tools:'工具箱',edit:'编辑火箭'} as Record<string,string>)[section]||'页面'}</strong></span><a href="#/tools/import"><span className={`connection-dot ${system.data?.ksp.connected?'connected':''}`}/>{system.data?.ksp.connected?'KSP 已连接':'连接 KSP'}<CircleHelp size={16}/></a></div>
    <main id="main-content" tabIndex={-1} className={section==='dictionary'?'dictionary-layout':'single-layout'}>
      {section==='dictionary'&&<aside><div className="section-heading"><h2>火箭档案</h2><button onClick={()=>void refresh().catch(e=>setError(String(e)))}>刷新</button></div><label className="search"><span className="sr-only">搜索火箭</span><input placeholder="搜索火箭" value={search} onChange={e=>setSearch(e.target.value)}/></label><h3>已建立曲线 · {rockets.length}</h3>{rockets.filter(r=>r.name.toLowerCase().includes(search.toLowerCase())).map(r=><button className={`craft ${slug===r.slug?'selected':''}`} key={r.slug} onClick={()=>navigate(`/dictionary/${r.slug}`)}><strong>{r.name}</strong><small>查看数据与曲线 →</small></button>)}</aside>}
      <section className="workspace">
        {(error||queryError)&&<div role="alert" className="alert error">{error||String(queryError)} <button onClick={()=>{setError('');void client.refetchQueries({type:'active'});}}>重试</button></div>}{busy&&<p className="loading-status" role="status">正在读取…</p>}
        <Suspense fallback={<p role="status" className="loading-status">正在加载工作模块…</p>}>
        {section==='dashboard'&&<>
          {heading('每一次发射，都心中有数。','从火箭档案到轨道任务，在一个工作空间中评估你的运力。')}
          <div className="overview-banner"><div><span className="eyebrow">MISSION CONTROL / 任务控制</span><h2>下一站，去哪里？</h2><p>给定载荷与目标轨道，找到适合这次任务的火箭。</p><a className="button" href="#/tools/mission">规划一次任务 <ArrowUpRight size={16}/></a></div><Orbit className="banner-orbit" strokeWidth={.65}/></div>
          <div className="dashboard-stats"><div className="card"><small><RocketIcon size={16}/> 火箭档案</small><strong>{rockets.length}</strong><a href="#/dictionary">查看所有数据 →</a></div><div className="card"><small><BookOpen size={16}/> 游戏火箭文件</small><strong>{crafts.length}</strong><a href="#/tools/import">导入新火箭 →</a></div><div className="card"><small><Activity size={16}/> 游戏连接</small><strong className="status-value">{system.isError?'读取失败':!system.data?'检测中':system.data.ksp.connected?'已连接':'待配置'}</strong><a href="#/tools/import">检查火箭来源 →</a></div></div>
          <div className="card"><div className="section-heading"><h2>火箭数据与曲线</h2><a href="#/dictionary">打开火箭档案 →</a></div>{!rockets.length&&!fleet.isPending&&<div className="empty"><RocketIcon size={32}/><h2>建立你的第一份火箭档案</h2><p>从游戏导入火箭，填写实测基准后即可查看运力曲线。</p><a className="button primary" href="#/tools/import">导入火箭</a></div>}{rockets.map(r=><a className="recent-row" key={r.slug} href={`#/dictionary/${r.slug}`}><strong>{r.name}</strong><span>浏览档案 →</span></a>)}</div>
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
          {slug==='import'&&<div className="card"><div className="section-heading"><h2>游戏中的火箭 · {crafts.length}</h2><button onClick={()=>void refresh().catch(e=>setError(String(e)))} disabled={busy}>刷新</button></div><label className="search"><span className="sr-only">搜索火箭或存档</span><input placeholder="搜索火箭或存档" value={search} onChange={e=>setSearch(e.target.value)}/></label><p className="muted">每 10 秒读取已保存的 VAB 火箭。点击导入后进入独立修改界面。</p><div className="import-grid">{library.data?.warnings.map((warning,i)=><p className="alert" key={i}>{warning}</p>)}{!crafts.length&&!library.isPending&&<div className="empty"><h2>暂未发现游戏火箭</h2><p>设置 .env 中的 C3_MASS_KSP_ROOT，在 VAB 保存火箭后刷新。</p></div>}{crafts.filter(c=>`${c.name} ${c.save} ${c.filename}`.toLowerCase().includes(search.toLowerCase())).map(c=><button className="craft" key={c.id} disabled={busy} onClick={()=>void importCraft(c)}><strong>{c.name||c.filename}</strong><small>{c.save} · {c.filename} · 点击导入</small></button>)}</div></div>}
        </>}
        {section==='edit'&&((slug==='new'?draft:config)?<RocketEditor key={slug} initial={(slug==='new'?draft:config)!} slug={slug==='new'?null:slug||null} onCancel={()=>navigate(slug==='new'?'/tools/import':`/dictionary/${slug}`)} onSaved={(savedSlug,result)=>{client.setQueryData(['curve',savedSlug],result);void client.invalidateQueries({queryKey:['rocket',savedSlug]});void refresh();navigate(`/dictionary/${savedSlug}`);}}/>:!busy&&<p>无法读取编辑对象，请返回火箭库选择。</p>)}
        {!['dashboard','dictionary','tools','edit'].includes(section)&&<p>页面不存在。<a href="#/dashboard">返回首页</a></p>}
        </Suspense>
      </section>
    </main>
    <footer className="workspace-footer"><span>C3-Mass · 本地运力工作空间</span><span>实测校准 / 理想燃烧模型</span></footer>
    </div>
  </div>;
}
