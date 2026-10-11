const fs = require('fs');
const path = require('path');
const {chromium} = require('playwright');
(async () => {
  const dir = path.resolve(process.argv[2]);
  const browser = await chromium.launch({headless:true, channel:'msedge'});
  try {
    const page = await browser.newPage({viewport:{width:1280,height:1000}});
    const errors=[];
    page.on('pageerror',e=>errors.push(e.message));
    await page.goto('file:///' + path.join(dir,'C3_mass.html').replaceAll('\\','/'));
    await page.waitForFunction(()=>document.querySelector('#curve').data?.length===3);
    await page.screenshot({path:path.join(dir,'C3_mass.png'),fullPage:true});
    const svg = await page.evaluate(()=>Plotly.toImage(document.querySelector('#curve'),{format:'svg',width:1200,height:800}));
    fs.writeFileSync(path.join(dir,'C3_mass.svg'),decodeURIComponent(svg.slice(svg.indexOf(',')+1)));
    await page.setViewportSize({width:390,height:844});
    await page.evaluate(()=>Plotly.Plots.resize(document.querySelector('#curve')));
    await page.waitForFunction(()=>document.documentElement.scrollWidth<=document.documentElement.clientWidth+1);
    await page.screenshot({path:path.join(dir,'C3_mass_mobile.png'),fullPage:true});
    const result=await page.evaluate(()=>({traces:document.querySelector('#curve').data.map(t=>({name:t.name,points:t.x.filter(v=>v!==null).length})),width:document.documentElement.clientWidth,scrollWidth:document.documentElement.scrollWidth}));
    fs.writeFileSync(path.join(dir,'plot-check.json'),JSON.stringify({errors,...result},null,2));
    console.log(JSON.stringify({errors,...result}));
    if(errors.length || result.scrollWidth>result.width+1)throw Error('Curve rendering failed');
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1});
