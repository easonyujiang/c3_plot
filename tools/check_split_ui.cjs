const {chromium}=require('playwright');
const fs=require('fs'),path=require('path'),assert=require('assert/strict');
const out=path.join(__dirname,'../output/automatic_import_20261010/split-browser');
fs.mkdirSync(out,{recursive:true});
(async()=>{
 const browser=await chromium.launch({headless:true,channel:'msedge'});
 const errors=[],results=[];
 try{
  for(const width of [390,768,1440]){
   const page=await browser.newPage({viewport:{width,height:1000}});
   page.on('pageerror',e=>errors.push(e.message));
   const mutations=[];
   page.on('request',r=>{if(['PUT','DELETE'].includes(r.method()))mutations.push(r.url());});
   await page.goto('http://127.0.0.1:8878/workspace/');
   await page.getByRole('heading',{name:'运力概览',exact:true}).waitFor();
   await page.getByRole('link',{name:/Neutron I 2630/}).waitFor();
   await page.screenshot({path:path.join(out,`dashboard-${width}.png`),fullPage:true});
   await page.getByRole('navigation',{name:'主导航'}).getByRole('link',{name:'火箭档案',exact:true}).click();
   await page.getByRole('button',{name:/Neutron I 2630/}).click();
   await page.getByRole('img',{name:'C3 与可用载荷曲线'}).waitFor();
   assert.equal(await page.locator('input[type=number]:not(.allowance input)').count(),0);
   assert.equal(await page.getByRole('button',{name:'保存并绘制曲线'}).count(),0);
   await page.screenshot({path:path.join(out,`dictionary-${width}.png`),fullPage:true});
   await page.getByRole('link',{name:'修改火箭数据'}).click();
   await page.getByRole('heading',{name:'修改火箭',exact:true}).waitFor();
   await page.getByLabel('火箭名称').fill('未保存的修改');
   await page.getByRole('button',{name:'取消并返回'}).click();
   await page.getByRole('heading',{name:'Neutron I 2630',exact:true}).waitFor();
   assert.equal(mutations.length,0);
   await page.getByRole('link',{name:'修改火箭数据'}).click();
   await page.getByRole('button',{name:'保存并绘制曲线'}).waitFor();
   const saved=page.waitForResponse(r=>r.request().method()==='PUT');
   await page.getByRole('button',{name:'保存并绘制曲线'}).click();
   assert((await saved).ok());
   await page.getByRole('heading',{name:'Neutron I 2630',exact:true}).waitFor();
   await page.getByRole('navigation',{name:'主导航'}).getByRole('link',{name:'工具箱',exact:true}).click();
   await page.getByRole('button',{name:'筛选火箭'}).click();
   const originalCandidate=page.locator('.candidate').filter({has:page.getByText('Neutron I 2630',{exact:true})});
   await originalCandidate.waitFor({timeout:60000});
   assert((await page.locator('.query-result').innerText()).includes('400 × 40000 km · 89°'));
   assert((await originalCandidate.innerText()).includes('Neutron I 2630'));
   assert((await originalCandidate.innerText()).includes('外推估计'));
   await page.screenshot({path:path.join(out,`acceptance-700kg-${width}.png`),fullPage:true});
   const size=await page.evaluate(()=>({scroll:document.documentElement.scrollWidth,width:innerWidth}));
   assert(size.scroll<=width+1);
   await page.getByRole('link',{name:'游戏火箭导入',exact:true}).click();
   await page.getByPlaceholder('搜索火箭或存档').fill('Neutrons');
   await page.getByRole('button',{name:/点击导入/}).first().click();
   await page.getByRole('heading',{name:'导入火箭',exact:true}).waitFor({timeout:60000});
   assert(await page.getByRole('button',{name:'保存并绘制曲线'}).isDisabled());
   results.push({width,dictionaryReadOnly:true,cancelWrites:false,editorSave:true,acceptanceCandidate:'Neutron I 2630',importMassGuard:true,size});
   await page.close();
  }
  assert.deepEqual(errors,[]);fs.writeFileSync(path.join(out,'checks.json'),JSON.stringify({results,errors},null,2));console.log(JSON.stringify({results,errors}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
