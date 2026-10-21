const {chromium}=require('playwright');
const fs=require('fs'),path=require('path'),assert=require('assert/strict');
const base=process.env.C3_TEST_URL || 'http://127.0.0.1:8878';
const slug=process.env.C3_TEST_ROCKET || 'neutron_i_2630';
const out=path.resolve(process.env.C3_BROWSER_OUTPUT || path.join(__dirname,'../../output/browser-checks'));
fs.mkdirSync(out,{recursive:true});
(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.C3_BROWSER_CHANNEL ? {channel:process.env.C3_BROWSER_CHANNEL} : {})}),results=[],errors=[];
 try{
  for(const width of [390,768,1440]){
   console.log('Checking chart width '+width);
   const page=await browser.newPage({viewport:{width,height:1000}});
   page.on('pageerror',e=>errors.push(e.message));
   await page.goto(`${base}/#/dictionary/${encodeURIComponent(slug)}`);
   const plot=page.getByRole('img',{name:'C3 与可用载荷曲线'});
   await page.waitForFunction(()=>document.querySelector('.capacity-plot')?._fullLayout?.xaxis);
   await plot.scrollIntoViewIfNeeded();
   const box=await plot.boundingBox();
   const point=await plot.evaluate(el=>{
    const trace=el.data.find(t=>t.x.some(x=>x!==null&&Math.abs(x)<5));
    const i=trace.x.findIndex(x=>x!==null&&Math.abs(x)<5);
    return {x:el._fullLayout.xaxis._offset+el._fullLayout.xaxis.l2p(trace.x[i]),
            y:el._fullLayout.yaxis._offset+el._fullLayout.yaxis.l2p(trace.y[i])};
   });
   await page.mouse.move(box.x+point.x,box.y+point.y);
   await page.locator('.hoverlayer .hovertext').waitFor();
   const hover=await page.locator('.hoverlayer').textContent();
   assert(hover.includes('净可用载荷')&&hover.includes('扣减前载荷')&&hover.includes('C3'));
   await page.screenshot({path:path.join(out,`hover-${width}.png`),fullPage:true});
   const original=await plot.evaluate(el=>el._fullLayout.xaxis.range.slice());
   await page.mouse.wheel(0,-400);
   await page.waitForFunction(original=>{
    const r=document.querySelector('.capacity-plot')._fullLayout.xaxis.range;
    return r[1]-r[0]<(original[1]-original[0])*.95;
   },original);
   const zoomed=await plot.evaluate(el=>el._fullLayout.xaxis.range.slice());
   await page.waitForTimeout(350); // Wait for Plotly's wheel gesture to finish.
   await plot.locator('[data-title="Pan"]').click();
   await page.waitForFunction(()=>document.querySelector('.capacity-plot')._fullLayout.dragmode==='pan');
   await plot.scrollIntoViewIfNeeded();
   const box2=await plot.boundingBox();
   const center=await plot.evaluate(el=>({x:el._fullLayout.xaxis._offset+el._fullLayout.xaxis._length*.5,y:el._fullLayout.yaxis._offset+el._fullLayout.yaxis._length*.5}));
   await page.mouse.move(box2.x+center.x,box2.y+center.y);await page.mouse.down();
   await page.mouse.move(box2.x+center.x+35,box2.y+center.y,{steps:8});await page.mouse.up();
   await page.waitForFunction(old=>Math.abs(document.querySelector('.capacity-plot')._fullLayout.xaxis.range[0]-old[0])>.01,zoomed);
   const panned=await plot.evaluate(el=>el._fullLayout.xaxis.range.slice());
   const before=await plot.evaluate(el=>el.data.map(t=>t.y.slice()));
   await page.getByLabel('扣减整流罩',{exact:true}).check();
   await page.waitForFunction(before=>JSON.stringify(document.querySelector('.capacity-plot').data.map(t=>t.y))!==JSON.stringify(before),before);
   const retained=await plot.evaluate(el=>el._fullLayout.xaxis.range.slice());
   assert.deepEqual(retained,panned,'Display update must preserve zoom and pan');
   const negative=await plot.evaluate(el=>el.data.flatMap(t=>t.y).some(v=>v!==null&&v<0));assert(!negative);
   await plot.scrollIntoViewIfNeeded();
   await plot.locator('[data-title="恢复全图"]').click();
   await page.waitForFunction(()=>document.querySelector('.capacity-plot')._fullLayout.xaxis.autorange===true);
   const fits=await plot.evaluate(el=>{
    const xs=el.data.flatMap(t=>t.x).filter(v=>v!==null),r=el._fullLayout.xaxis.range;
    return r[0]<=Math.min(...xs)&&r[1]>=Math.max(...xs);
   });assert(fits,'Reset must fit the currently displayed curve');
   await plot.locator('[data-title="Zoom"]').click();
   await page.waitForFunction(()=>document.querySelector('.capacity-plot')._fullLayout.dragmode==='zoom');
   await plot.scrollIntoViewIfNeeded();
   const axes=await plot.evaluate(el=>({x:el._fullLayout.xaxis._offset,y:el._fullLayout.yaxis._offset,w:el._fullLayout.xaxis._length,h:el._fullLayout.yaxis._length}));
   const zoomBox=await plot.boundingBox();
   await page.mouse.move(zoomBox.x+axes.x+axes.w*.2,zoomBox.y+axes.y+axes.h*.2);await page.mouse.down();
   await page.mouse.move(zoomBox.x+axes.x+axes.w*.7,zoomBox.y+axes.y+axes.h*.7,{steps:8});await page.mouse.up();
   await page.waitForFunction(()=>document.querySelector('.capacity-plot')._fullLayout.xaxis.autorange===false);
   await page.waitForTimeout(400); // Separate the box-drag gesture from the double tap.
   await plot.scrollIntoViewIfNeeded();
   const restoredBox=await plot.boundingBox();
   const restorePoint=await plot.evaluate(el=>({x:el._fullLayout.xaxis._offset+el._fullLayout.xaxis._length*.5,y:el._fullLayout.yaxis._offset+el._fullLayout.yaxis._length*.5}));
   await page.mouse.dblclick(restoredBox.x+restorePoint.x,restoredBox.y+restorePoint.y,{delay:50});
   await page.waitForFunction(()=>document.querySelector('.capacity-plot')._fullLayout.xaxis.autorange===true);
   const dimensions=await page.evaluate(()=>({viewport:innerWidth,scroll:document.documentElement.scrollWidth}));assert(dimensions.scroll<=width+1);
   const writes=[];
   page.on('request',r=>{if(['PUT','DELETE'].includes(r.method()))writes.push(r.url());});
   await page.getByRole('link',{name:'修改火箭数据'}).click();
   await page.getByRole('heading',{name:'修改火箭',exact:true}).waitFor();
   await page.getByLabel('火箭名称').fill('未保存的修改');
   await page.getByRole('button',{name:'取消并返回'}).click();
   await page.getByRole('img',{name:'C3 与可用载荷曲线'}).waitFor();
   assert.deepEqual(writes,[],'Cancelling an edit must not write rocket data');
   await page.getByRole('navigation',{name:'主导航'}).getByRole('link',{name:'工具箱',exact:true}).click();
   await page.getByRole('button',{name:'筛选火箭'}).click();
   await page.locator('.query-result').waitFor();
   assert((await page.locator('.mission-energy').innerText()).includes('-15.001'));
   assert((await page.locator('.mission-energy').innerText()).includes('-5.667'));
   assert(!(await page.locator('.candidate').innerText()).includes('尚无该载荷下的实测支持'));
   await page.screenshot({path:path.join(out,`mission-${width}.png`),fullPage:true});
   results.push({width,hover:true,wheelZoom:true,pan:true,reset:true,boxZoom:true,doubleClickReset:true,fairingPreservesZoom:true,equivalentC3:true,conciseResults:true,dimensions});
   await page.close();
  }
  assert.deepEqual(errors,[]);fs.writeFileSync(path.join(out,'checks.json'),JSON.stringify({results,errors},null,2));console.log(JSON.stringify({results,errors}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
