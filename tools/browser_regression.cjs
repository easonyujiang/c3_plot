const {chromium} = require('playwright');
const fs = require('fs');
const path = require('path');
const out=process.env.C3_BROWSER_OUTPUT || path.join(__dirname,'..','output','browser-regression');
fs.mkdirSync(out,{recursive:true});
const base=process.env.C3_TEST_URL || 'http://127.0.0.1:8877';
let browser;
(async()=>{
  browser=await chromium.launch({headless:true,channel:'msedge'});
  const errors=[];
  const checks=[];
  for(const width of [390,768,1280]) {
    const page=await browser.newPage({viewport:{width,height:844}});
    page.on('pageerror',e=>errors.push(e.message));
    page.on('console',m=>{if(['error','warning'].includes(m.type())) errors.push(m.text());});
    await page.goto(base);
    await page.waitForFunction(()=>window.__C3_APP_READY);
    await page.evaluate(async()=>{const app=await import('/js/app.js?v=2026100901');app.showPlanner();});
    await page.locator('#oc-alt').fill('35786');
    await page.locator('#oc-inc').fill('0');
    await page.locator('#oc-run').click();
    await page.waitForSelector('.capacity-table',{timeout:60000});
    for(const expanded of [false,true]){
      if(expanded) await page.locator('.fleet-details').evaluateAll(ds=>ds.forEach(d=>d.open=true));
      const dims=await page.evaluate(()=>({viewport:document.documentElement.clientWidth,documentWidth:document.documentElement.scrollWidth,timelines:document.querySelectorAll('.timeline-table tbody tr').length,resultWidth:document.querySelector('.orbit-results').clientWidth,local: [...document.querySelectorAll('.capacity-section')].map(e=>({width:e.clientWidth,scroll:e.scrollWidth}))}));
      checks.push({width,expanded,...dims});
      if(dims.documentWidth>dims.viewport+1 || dims.local.some(e=>e.width<dims.resultWidth-3)) throw Error(`Overflow or compressed table ${JSON.stringify(checks.at(-1))}`);
      await page.screenshot({path:path.join(out,`planner-${width}-${expanded?'expanded':'collapsed'}.png`),fullPage:true});
    }
    {
      await page.evaluate(async()=>{const a=await import('/js/app.js?v=2026100901');await a.selectRocket('neutron_i_2630');});
      await page.waitForSelector('#overview-grid .db-card');
      const overview=await page.evaluate(()=>({viewport:document.documentElement.clientWidth,width:document.documentElement.scrollWidth,visible:document.getElementById('view-home').style.display!=='none',source:document.getElementById('overview-grid').textContent.includes('历史数据，来源未回填')}));
      checks.push({width,view:'overview',...overview});
      if(!overview.visible || !overview.source || overview.width>overview.viewport+1) throw Error(`Overview failed ${JSON.stringify(overview)}`);
      await page.screenshot({path:path.join(out,`overview-${width}.png`),fullPage:true});
    }
    await page.close();
  }
  const request=await browser.newContext();
  const response=await request.request.post(base+'/api/mission-query',{data:{target_altitude_km:35786,inclination_deg:0,parking_alt_km:220,launch_lat:19.81,payload_t:0,orbit_type:'circular',arrival_strategy:'combined',include_audit:true}});
  const mission=await response.json();
  fs.writeFileSync(path.join(out,'geo-query.json'),JSON.stringify(mission,null,2));
  fs.writeFileSync(path.join(out,'browser-check.json'),JSON.stringify({checks,errors,queryStatus:response.status()},null,2));
  console.log(JSON.stringify({checks,errors,queryStatus:response.status()}));
  await browser.close();
  if(errors.length) process.exitCode=1;
})().catch(async e=>{console.error(e);if(browser) await browser.close();process.exitCode=1});
