const {chromium}=require('playwright');
const path=require('node:path'),assert=require('node:assert/strict');
const server=require('./browser_static.cjs').createServer(path.resolve(__dirname,'..'));
(async()=>{
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const browser=await chromium.launch({headless:true,executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE});
  try{
    const page=await browser.newPage({viewport:{width:390,height:844},reducedMotion:'reduce'});
    const errors=[],apiRequests=[];
    page.on('pageerror',error=>errors.push(error.message));
    page.on('request',request=>{if(request.url().includes('/api/v1/'))apiRequests.push(request.url());});
    await page.route('https://telegram.org/**',route=>route.fulfill({body:''}));
    let releaseTheme;
    const themeWait=new Promise(resolve=>releaseTheme=resolve);
    await page.route('**/shared/store-themes.json',async route=>{await themeWait;await route.continue();});
    const base=`http://127.0.0.1:${server.address().port}`;
    await page.goto(base+'/miniapp/?preview=1',{waitUntil:'domcontentloaded'});
    assert.equal(await page.locator('html').evaluate(n=>n.classList.contains('booting')),true);
    assert.equal(await page.locator('#loadingView').isVisible(),true);
    assert.equal(await page.locator('.topbar').isVisible(),false);
    assert.equal(await page.locator('#previewBanner').isVisible(),false);
    await page.screenshot({path:'/tmp/ghbf-miniapp-skeleton.png'});
    releaseTheme();
    await page.locator('#authenticatedApp').waitFor({state:'visible'});
    assert.equal(await page.locator('html').getAttribute('data-store-theme'),'emerald');
    assert.equal(await page.locator('#loadingView').isVisible(),false);
    assert.equal(await page.locator('#previewBanner').isVisible(),true);
    await page.screenshot({path:'/tmp/ghbf-miniapp-preview-mobile.png'});
    await page.goto(base+'/miniapp/');
    await page.waitForURL('**/miniapp/?preview=1');
    await page.locator('#authenticatedApp').waitFor({state:'visible'});
    for(const theme of ['midnight','pearl','ocean','rose','emerald']){
      await page.locator('#previewTheme').selectOption(theme);
      await page.locator('#authenticatedApp').waitFor({state:'visible'});
      await page.waitForFunction(key=>document.documentElement.dataset.storeTheme===key,theme);
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
    }
    await page.locator('#storeLanguage').selectOption('ar');
    assert.equal(await page.locator('html').getAttribute('dir'),'rtl');
    await page.locator('[data-target="help"]').click();
    assert.equal(await page.locator('#helpView').isVisible(),true);
    await page.screenshot({path:'/tmp/ghbf-miniapp-help-arabic.png'});
    await page.setViewportSize({width:1440,height:1000});
    await page.locator('[data-target="shop"]').click();
    await page.screenshot({path:'/tmp/ghbf-miniapp-preview-desktop.png'});
    assert.deepEqual(apiRequests,[],'Preview must never contact business APIs');
    await page.goto(base+'/miniapp/?bot_id=invalid-bot-link');
    await page.locator('#errorView').waitFor({state:'visible'});
    assert.equal(new URL(page.url()).searchParams.has('preview'),false);
    assert.equal(await page.locator('html').getAttribute('data-store-theme'),'emerald');
    assert.equal(await page.locator('#errorView a').getAttribute('href'),'/miniapp/?preview=1');
    assert.deepEqual(errors,[]);
    console.log('MiniApp skeleton, entry, themes, RTL, mobile and authentication boundary passed');
  }finally{await browser.close();server.close();}
})().catch(error=>{console.error(error);process.exitCode=1;server.close();});
