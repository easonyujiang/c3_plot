const {chromium}=require('playwright');
const fs=require('fs'),path=require('path'),assert=require('assert/strict');
const out=path.join(__dirname,'../output/automatic_import_20261010/modern-browser');
fs.mkdirSync(out,{recursive:true});
(async()=>{
 const browser=await chromium.launch({headless:true,channel:'msedge'});
 const errors=[],results=[];
 try{
  for(const width of [390,768,1440]){
   const page=await browser.newPage({viewport:{width,height:1000}});
   page.on('pageerror',e=>errors.push(e.message));
   await page.goto('http://127.0.0.1:8878/workspace/');
   await page.getByRole('button',{name:/Neutron I 2630/}).click();
   await page.getByRole('img',{name:'C3 与可用载荷曲线'}).waitFor();
   await page.getByLabel('扣减整流罩').check();
   await page.getByLabel('整流罩质量 / t').fill('0.2');
   await page.screenshot({path:path.join(out,`workspace-${width}.png`),fullPage:true});
   const dimension=await page.evaluate(()=>({scroll:document.documentElement.scrollWidth,width:innerWidth}));
   assert(dimension.scroll<=width+1,JSON.stringify(dimension));
   if(width===1440){
    await page.getByRole('button',{name:'查询',exact:true}).click();
    await page.locator('.query-result').waitFor({timeout:60000});
    assert((await page.locator('.query-result').innerText()).includes('估计可用载荷'));
    const responsePromise=page.waitForResponse(r=>r.url().includes('/api/rockets/neutron_i_2630')&&r.request().method()==='PUT');
    await page.getByRole('button',{name:'保存并绘制曲线'}).click();
    const response=await responsePromise;assert(response.ok(),await response.text());
    await page.getByText('基准点已保存，曲线已更新。').waitFor();
    await page.getByRole('img',{name:'C3 与可用载荷曲线'}).waitFor();
    await page.screenshot({path:path.join(out,'workspace-saved-1440.png'),fullPage:true});
   }
   await page.getByPlaceholder('搜索火箭或存档').fill('Neutrons');
   await page.getByRole('button',{name:/点击导入/}).first().click();
   await page.getByText('已读取火箭文件；动态质量确认完成后才能计算。').waitFor({timeout:60000});
   assert(await page.getByRole('button',{name:'保存并绘制曲线'}).isDisabled());
   results.push({width,dimension,importMassGuard:true});await page.close();
  }
  assert.deepEqual(errors,[]);fs.writeFileSync(path.join(out,'checks.json'),JSON.stringify({results,errors},null,2));
  console.log(JSON.stringify({results,errors}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
