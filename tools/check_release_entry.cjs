// Read-only acceptance against the user's real database; no save/edit requests.
const {chromium}=require('playwright');
const assert=require('assert/strict'),fs=require('fs'),path=require('path');
const out=path.resolve(__dirname,'../output/automatic_import_20261011/release-entry');
fs.mkdirSync(out,{recursive:true});
(async()=>{
 const browser=await chromium.launch({headless:true,channel:'msedge'});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  page.on('request',r=>{assert(!['PUT','DELETE'].includes(r.method()));if(r.method()==='POST')assert(r.url().endsWith('/api/mission-query'));});
  await page.goto('http://127.0.0.1:8765/');
  await page.getByRole('heading',{name:'运力概览',exact:true}).waitFor();
  await page.getByRole('link',{name:/Neutron I 2630/}).waitFor();
  const rockets=await(await page.request.get('http://127.0.0.1:8765/api/rockets')).json();
  assert.equal(rockets.length,1);assert.equal(rockets[0].slug,'neutron_i_2630');
  await page.getByRole('navigation',{name:'主导航'}).getByRole('link',{name:'工具箱',exact:true}).click();
  const queried=page.waitForResponse(r=>r.url().endsWith('/api/mission-query'));
  await page.getByRole('button',{name:'筛选火箭'}).click();
  const response=await queried;assert(response.ok());
  const result=await response.json();
  const candidate=result.results.find(r=>r.slug==='neutron_i_2630');
  assert(candidate.verified_feasible);assert(candidate.verified_capacity>.7);
  assert.equal(candidate.detailed_validation.calibration_support.status,'extrapolated');
  assert.equal(candidate.recommendation_eligible,false);
  await page.locator('.candidate').waitFor();
  await page.locator('.candidate details').evaluate(el=>el.open=true);
  await page.screenshot({path:path.join(out,'700kg-original-database.png'),fullPage:true});
  assert.deepEqual(errors,[]);
  fs.writeFileSync(path.join(out,'result.json'),JSON.stringify({defaultNewFrontend:true,formalRocketCount:1,errors,...result},null,2));
  console.log(JSON.stringify({defaultNewFrontend:true,formalRocketCount:1,capacity_t:candidate.verified_capacity,recommendation_eligible:candidate.recommendation_eligible,errors}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
