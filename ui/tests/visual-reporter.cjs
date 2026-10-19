// A visual page reports its height to the app only once it has a width: a frame laid out at zero width would report
// an inflated height the app cannot shrink back from. The script is the one the server serves, under its policy.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const reporter = fs.readFileSync(path.join(__dirname, '..', '..', 'backend', 'visual-reporter.js'), 'utf8').trim();
const policy = `default-src 'none'; style-src 'unsafe-inline'; script-src 'sha256-${crypto.createHash('sha256').update(reporter).digest('base64')}'`;
const visual = `<!doctype html><p style="margin:0;font:16px/20px sans-serif">${'A long line of words that wraps. '.repeat(20)}</p><script>${reporter}</script>`;
(async()=>{
  const browser=await chromium.launch({headless:true,channel:process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
   // Once as is, and once in a browser without ResizeObserver, where the window's resize event is the only signal.
   for(const observer of [true,false]){
    const page=await browser.newPage({viewport:{width:800,height:600},serviceWorkers:'block'});
    if(!observer)await page.addInitScript(()=>{delete window.ResizeObserver;});
    await page.route('**/*',route=>{
      const url=new URL(route.request().url());
      if(url.host==='visuals.test')return route.fulfill({contentType:'text/html',headers:{'content-security-policy':policy},body:visual});
      return route.fulfill({contentType:'text/html',body:`<!doctype html><script>window.seen=[];addEventListener('message',e=>seen.push(e.data))</script>
        <iframe id="v" sandbox="allow-scripts" style="border:0;width:0;height:100px" src="https://visuals.test/v"></iframe>`});
    });
    await page.goto('https://tico-ui.test/');
    const frame=await (await page.waitForSelector('#v',{state:'attached'})).contentFrame();
    await frame.waitForLoadState('load');
    // After two frames any load or resize report has been posted; a marker from the same page arrives after it.
    const zeroHeight=await frame.evaluate(()=>new Promise(done=>requestAnimationFrame(()=>requestAnimationFrame(()=>{
      parent.postMessage({marker:1},'*');done(Math.ceil(document.documentElement.scrollHeight));}))));
    await page.waitForFunction(()=>seen.some(m=>m.marker));
    assert.deepEqual(await page.evaluate(()=>seen.filter(m=>'ticoVisualHeight' in m)),[]);
    console.log(`a frame at zero width sends no height${observer?'':' (no ResizeObserver)'}`);

    await page.evaluate(()=>{seen.length=0;document.getElementById('v').style.width='320px';});
    await page.waitForFunction(()=>seen.some(m=>'ticoVisualHeight' in m));
    const real=await frame.evaluate(()=>Math.ceil(document.documentElement.scrollHeight));
    assert.ok(real<zeroHeight,`the zero-width height ${zeroHeight} should be the inflated one, not ${real}`);
    assert.equal(await page.evaluate(()=>seen.filter(m=>'ticoVisualHeight' in m).at(-1).ticoVisualHeight),real);
    console.log(`given a width it sends its real height${observer?'':' (no ResizeObserver)'}`);
    await page.close();
   }
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
