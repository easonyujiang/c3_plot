export type PlotlyApi = {
  react:(element:HTMLElement,traces:Record<string,unknown>[],layout:Record<string,unknown>,config:Record<string,unknown>)=>Promise<unknown>;
  purge:(element:HTMLElement)=>void;
  relayout:(element:HTMLElement,update:Record<string,unknown>)=>Promise<unknown>;
  Icons:{home:unknown};
  Plots:{resize:(element:HTMLElement)=>Promise<unknown>};
};
declare global {interface Window {Plotly?:PlotlyApi}}
let pending:Promise<PlotlyApi>|undefined;
export function loadPlotly():Promise<PlotlyApi> {
  if(window.Plotly)return Promise.resolve(window.Plotly);
  if(!pending)pending=new Promise<PlotlyApi>((resolve,reject)=>{
    const script=document.createElement('script');
    script.src='/static/plotly.min.js';script.async=true;
    script.onload=()=>window.Plotly?resolve(window.Plotly):reject(new Error('图表库未正确加载'));
    script.onerror=()=>{script.remove();reject(new Error('交互图表加载失败，请刷新页面'));};
    document.head.appendChild(script);
  }).catch(error=>{pending=undefined;throw error;});
  return pending;
}
