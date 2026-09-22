const {chromium}=require('playwright');
const path=require('node:path'),assert=require('node:assert/strict');
const server=require('./browser_static.cjs').createServer(path.resolve(__dirname,'..'));
(async()=>{
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const browser=await chromium.launch({headless:true,executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE});
  try{
    const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[],writes=[];
    await page.addInitScript(()=>localStorage.setItem('ghbf_admin_token','isolated-session'));
    page.on('pageerror',error=>errors.push(error.message));
    await page.route('https://telegram.org/**',route=>route.fulfill({body:''}));
    await page.route('**/api/v1/**',async route=>{
      const request=route.request(),url=new URL(request.url());let body={};
      if(request.method()!=='GET')writes.push({path:url.pathname,body:request.postDataJSON()});
      if(url.pathname.endsWith('/admin/bootstrap'))body={store:{name:'Sample',id:'tenant'},actor:{role:'OWNER',id:'owner',first_name:'Owner'},counts:{products:0,active_products:0,orders:0,attention_orders:0,dead_letter_jobs:0,financial_open_cases:0,reconciliation_reviews:0}};
      else if(url.pathname.endsWith('/categories'))body=[];
      else if(url.pathname.endsWith('/onboarding/checklist'))body={items:[],progress_percent:0};
      else if(url.pathname.endsWith('/admin/attention'))body={totals:{backup:1},checks:[{key:'catalog',label:'Active products',configured:false,view:'products'}],alerts:[{key:'backup-evidence',fingerprint:'a'.repeat(64),title:'Backup evidence needs attention',guidance:'Record database and vault backup evidence.',view:'store-settings',record_id:'installation',acknowledged:writes.some(w=>w.path.endsWith('/acknowledge'))}]};
      else if(url.pathname.endsWith('/admin/store-settings'))body={version:request.method()==='PUT'?1:0,name:'Sample',sales_paused:false,faq:[]};
      else if(url.pathname.endsWith('/admin/installation'))body={reported:{version:0,backup_status:'NOT_RECORDED'},observed_schema_revisions:['c93eb541da62']};
      else if(url.pathname.endsWith('/workspace/overview'))body={projects:[{id:'handoff',licensed_to:'Sample Customer',licensed_domain:'example.invalid',status:'PREPARING',created_at:'2026-09-22',version_tag:'v1',release_selected:true,snapshot_generated:false,acceptance_recorded:false,open_issues:2}]};
      return route.fulfill({json:body});
    });
    await page.goto(`http://127.0.0.1:${server.address().port}/admin/`);
    await page.locator('#app').waitFor({state:'visible'});
    await page.locator('[data-view="attention"]').first().click();
    await page.getByRole('button',{name:'Acknowledge for 24 hours'}).click();
    await page.getByText('Acknowledged · still open').waitFor();
    await page.getByRole('button',{name:'Open resolution workspace'}).click();
    await page.locator('#settingSalesPaused').check();
    await page.locator('#saveStoreSettings').click();
    await page.waitForFunction(()=>document.getElementById('storeSettingsSaved').textContent.includes('Saved'));
    assert.equal(writes.find(w=>w.path.endsWith('/store-settings')).body.sales_paused,true);
    await page.locator('[data-installation-open]').click();
    await page.locator('#installationEvidenceForm').waitFor();
    assert.match(await page.locator('#installationDialog').textContent(),/c93eb541da62/);
    await page.locator('[data-install-close]').click();
    await page.locator('button[data-view="delivery"]').evaluate(n=>{const details=n.closest('details');if(details)details.open=true;});
    await page.locator('button[data-view="delivery"]').click();
    await page.getByText('Acceptance pending · 2 open issues').waitFor();
    await page.screenshot({path:'/tmp/ghbf-delivery-final.png'});
    assert.deepEqual(errors,[]);
    console.log('Hardening browser passed: attention acknowledgement, settings pause, installation evidence and delivery progress');
  }finally{await browser.close();server.close();}
})().catch(error=>{console.error(error);process.exitCode=1;server.close();});
