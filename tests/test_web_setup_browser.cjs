/* First-run browser states; API authorization is exercised by Python integration tests. */
const { chromium } = require('playwright');
const fs = require('node:fs'), http = require('node:http'), path = require('node:path'), assert = require('node:assert/strict');
const root = path.resolve(__dirname, '..');
const server = http.createServer((req, res) => {
  const match = new URL(req.url, 'http://local').pathname.match(/^\/(setup|shared|admin|build)\/([a-z.-]+)?$/);
  if (!match) { res.writeHead(404); return res.end(); }
  const file = path.join(root, 'apps', match[1] === 'build' ? 'configurator' : match[1], 'static', match[2] || 'index.html');
  if (!fs.existsSync(file)) { res.writeHead(404); return res.end(); }
  res.setHeader('Content-Type', file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : file.endsWith('.png') ? 'image/png' : 'text/html');
  res.end(fs.readFileSync(file));
});
(async () => {
  await new Promise(r => server.listen(0, '127.0.0.1', r));
  const browser = await chromium.launch({headless:true, executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE});
  try {
    const page = await browser.newPage({viewport:{width:1440,height:1100},reducedMotion:'reduce'});
    const errors=[]; page.on('pageerror',e=>errors.push(e.message));
    await page.route('https://telegram.org/**',r=>r.fulfill({body:''}));
    let initialized=false, available=false, ready=true, writes=[], failWrite=true;
    await page.route('**/api/v1/setup/**', async route => {
      if (route.request().method() === 'GET') return route.fulfill({status:available?200:503,json:{initialized,ready_for_setup:ready}});
      writes.push(route.request().postDataJSON());
      return route.fulfill({status:failWrite?403:200,json:failWrite?{detail:'Setup code is invalid.'}:{status:'configured'}});
    });
    const base = `http://127.0.0.1:${server.address().port}`;
    await page.goto(base+'/setup/?code=test-code');
    await page.locator('#unavailableCard').waitFor({state:'visible'});
    assert.equal(new URL(page.url()).search,'');
    available=true;
    await page.locator('#retryStatus').click();
    await page.locator('#setupForm').waitFor({state:'visible'});
    assert.equal(await page.locator('#setupCode').inputValue(),'test-code');
    await page.locator('#tenantName').fill('GH Store Factory');
    assert.equal(await page.locator('#tenantSlug').inputValue(),'gh-store-factory');
    await page.locator('#ownerUsername').fill('factoryowner');
    await page.locator('#ownerPassword').fill('test-only-password');
    await page.locator('#confirmPassword').fill('different-password');
    await page.locator('#submitButton').click();
    assert.equal(writes.length,0);
    assert((await page.locator('#errorBox').textContent()).includes('match'));
    await page.locator('#confirmPassword').fill('test-only-password');
    await page.locator('#togglePassword').click();
    assert.equal(await page.locator('#ownerPassword').getAttribute('type'),'text');
    await page.locator('#submitButton').click();
    await page.waitForFunction(()=>document.querySelector('#errorBox').textContent.includes('invalid'));
    assert.equal(await page.locator('#ownerPassword').inputValue(),'');
    assert.equal(await page.locator('#tenantName').inputValue(),'GH Store Factory');
    assert(!('bot_token' in writes[0]) && !('owner_telegram_id' in writes[0]));
    await page.screenshot({path:'/tmp/ghbf-web-setup-desktop.png',fullPage:true});
    for (const width of [320,375,768]) {
      await page.setViewportSize({width,height:850});
      assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`overflow ${width}`);
    }
    await page.setViewportSize({width:375,height:850});
    await page.screenshot({path:'/tmp/ghbf-web-setup-mobile.png',fullPage:true});
    failWrite=false;
    await page.locator('#ownerPassword').fill('test-only-password');
    await page.locator('#confirmPassword').fill('test-only-password');
    await page.locator('#submitButton').click();
    await page.locator('#successCard').waitFor({state:'visible'});
    assert.equal(await page.locator('#setupCode').inputValue(),'');
    assert.equal(await page.evaluate(()=>localStorage.length+sessionStorage.length),0);
    initialized=true; await page.reload();
    await page.locator('#doneCard').waitFor({state:'visible'});
    assert(await page.locator('#setupForm').isHidden());
    initialized=false;ready=false;await page.reload();
    await page.locator('#prerequisiteBox').waitFor({state:'visible'});
    assert(await page.locator('#submitButton').isDisabled());
    await page.setViewportSize({width:1440,height:1000});
    // A single primitive drives each factory surface; logo must load locally.
    for (const route of ['/setup/','/admin/','/build/']) {
      await page.goto(base+route);
      await page.locator('.gh-logo').first().waitFor({state:'visible'});
      assert(await page.locator('.gh-logo').first().evaluate(i=>i.complete&&i.naturalWidth>0));
      await page.evaluate(()=>document.documentElement.style.setProperty('--gh-brand','#123456'));
      assert.equal(await page.locator('.gh-logo').first().evaluate(i=>getComputedStyle(i).backgroundColor),'rgb(18, 52, 86)');
      const action = route === '/setup/' ? '#submitButton' : route === '/admin/' ? '#passwordLoginSubmit' : '.hero-cta';
      assert.equal(await page.locator(action).evaluate(i=>getComputedStyle(i).backgroundColor),'rgb(18, 52, 86)');
    }
    assert.deepEqual(errors,[]);
    console.log('Web setup browser passed: unavailable/retry, validation, failure/retry, no bot fields, success, initialized, prerequisites, mobile, shared theme/logo.');
  } finally {await browser.close();server.close();}
})().catch(e=>{console.error(e);server.close();process.exitCode=1;});
