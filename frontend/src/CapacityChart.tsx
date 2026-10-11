import { useEffect, useRef, useState } from 'react';
import type { CurveData } from './types';
import { loadPlotly } from './plotly';

type Point={c3:number;payload:number};
type ChartHost=HTMLDivElement&{on?:(name:string,callback:()=>void)=>void;_c3ResetInstalled?:boolean};
function tracesFor(data:CurveData,fairing:number):Record<string,unknown>[] {
  const raw=data.c3_mass_curve, support=data.calibration_support;
  const lines=[{name:'基准载荷范围内',color:'#20706b',dash:'solid'},
    {name:'范围外推算',color:'#b77d38',dash:'dash'}].map(style=>({...style,x:[] as (number|null)[],y:[] as (number|null)[],gross:[] as (number|null)[]}));
  for(let i=1;i<raw.length;i++){
    const a=raw[i-1],b=raw[i];
    const points:Point[]=[a,b];
    if(a.payload!==b.payload)for(const level of [fairing,support?.measured_payload_min_t,support?.measured_payload_max_t]){
      if(level!==undefined&&level>Math.min(a.payload,b.payload)&&level<Math.max(a.payload,b.payload))
        points.push({c3:a.c3+(b.c3-a.c3)*(level-a.payload)/(b.payload-a.payload),payload:level});
    }
    points.sort((p,q)=>p.c3-q.c3);
    for(let j=1;j<points.length;j++){
      const p=points[j-1],q=points[j],mid=(p.payload+q.payload)/2;
      if(mid<fairing)continue;
      const measured=!!support&&mid>=support.measured_payload_min_t&&mid<=support.measured_payload_max_t;
      const line=lines[measured?0:1];
      // Continue adjacent segments; null separates disjoint regions.
      if(line.x.at(-1)!==p.c3){line.x.push(null,p.c3);line.y.push(null,Math.max(0,p.payload-fairing));line.gross.push(null,p.payload);}
      line.x.push(q.c3);line.y.push(Math.max(0,q.payload-fairing));line.gross.push(q.payload);
    }
  }
  return lines.filter(line=>line.x.length).map(line=>({
    type:'scatter',mode:'lines',name:line.name,x:line.x,y:line.y,customdata:line.gross,
    line:{color:line.color,width:3,dash:line.dash},connectgaps:false,
    hovertemplate:'C3：%{x:.3f} km²/s²<br>净可用载荷：%{y:.3f} t<br>扣减前载荷：%{customdata:.3f} t'+
      `<br>整流罩扣减：${fairing.toFixed(3)} t<extra>${line.name}</extra>`,
  }));
}
export function CapacityChart({data,fairing}:{data:CurveData;fairing:number}) {
  const host=useRef<ChartHost>(null);
  const [error,setError]=useState(''),[ready,setReady]=useState(false);
  useEffect(()=>{
    const element=host.current;if(!element)return;
    let active=true;
    setError('');
    void loadPlotly().then(async plotly=>{
      if(!active)return;
      const raw=data.c3_mass_curve;
      await plotly.react(element,tracesFor(data,fairing),{
        height:420,autosize:true,margin:{l:62,r:20,t:35,b:60},
        paper_bgcolor:'#fff',plot_bgcolor:'#fff',font:{family:'Segoe UI, Microsoft YaHei, sans-serif',color:'#687584',size:12},
        xaxis:{title:{text:'C3 / km²·s⁻²'},range:[Math.min(...raw.map(p=>p.c3)),Math.max(...raw.map(p=>p.c3))],gridcolor:'#e7edf1',zerolinecolor:'#cbd7dc',automargin:true,showspikes:true,spikemode:'across',spikecolor:'#90a7a5',spikethickness:1},
        yaxis:{title:{text:'净可用载荷 / t'},range:[0,Math.max(...raw.map(p=>p.payload))*1.04],rangemode:'tozero',gridcolor:'#e7edf1',zerolinecolor:'#cbd7dc',automargin:true},
        hovermode:'closest',hoverdistance:40,dragmode:'zoom',showlegend:false,
        uirevision:JSON.stringify([raw[0],raw.at(-1),raw.length]),
      },{scrollZoom:true,responsive:true,displayModeBar:true,displaylogo:false,showTips:false,doubleClick:'reset',
        modeBarButtonsToRemove:['select2d','lasso2d','resetScale2d'],
        modeBarButtonsToAdd:[{name:'恢复全图',icon:plotly.Icons.home,
          click:(graph:HTMLElement)=>void plotly.relayout(graph,{'xaxis.autorange':true,'yaxis.autorange':true})}],
        toImageButtonOptions:{format:'png',filename:'rocket-capacity',scale:2}});
      // Plotly's temporary drag cover can consume the native DOM dblclick.
      // Use its gesture event and reset after its own range handling finishes.
      if(!element._c3ResetInstalled&&element.on){
        element._c3ResetInstalled=true;
        element.on('plotly_doubleclick',()=>queueMicrotask(()=>{
          if(element.isConnected)void plotly.relayout(element,{'xaxis.autorange':true,'yaxis.autorange':true});
        }));
      }
      if(active)setReady(true);
    }).catch(e=>{if(active)setError(e instanceof Error?e.message:String(e));});
    return()=>{active=false;};
  },[data,fairing]);
  useEffect(()=>{
    const element=host.current;if(!element)return;
    const observer=new ResizeObserver(()=>{if(window.Plotly&&element.classList.contains('js-plotly-plot'))void window.Plotly.Plots.resize(element).catch(()=>{});});
    observer.observe(element);
    return()=>{observer.disconnect();window.Plotly?.purge(element);};
  },[]);
  if(!data.c3_mass_curve.length)return <p>暂无曲线数据</p>;
  return <div className="capacity-chart">
    <p className="muted chart-help">滚轮缩放 · 拖动画框放大 · 工具栏切换平移 · 双击恢复全图；移动光标查看数值。</p>
    {error&&<p role="alert" className="error">{error}</p>}
    {!ready&&!error&&<p className="muted">正在加载交互曲线…</p>}
    <div ref={host} className="capacity-plot" role="img" aria-label="C3 与可用载荷曲线"
      onDoubleClickCapture={()=>queueMicrotask(()=>{
        if(host.current)void window.Plotly?.relayout(host.current,{'xaxis.autorange':true,'yaxis.autorange':true});
      })}/>
  </div>;
}
