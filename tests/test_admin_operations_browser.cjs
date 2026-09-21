const {chromium}=require('playwright');
const fs=require('node:fs'), http=require('node:http'), path=require('node:path'), assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..');
const server=http.createServer((req,res)=>{
  const sharedName = new URL(req.url, 'http://local').pathname.match(/^\/shared\/([a-z.-]+)$/)?.[1];
  if (sharedName) {
    const sharedFile = path.join(root, 'apps/shared/static', sharedName);
    if (!fs.existsSync(sharedFile)) { res.writeHead(404); return res.end(); }
    res.setHeader('Content-Type', sharedName.endsWith('.css') ? 'text/css' : 'image/png');
    return res.end(fs.readFileSync(sharedFile));
  }

 const file=new URL(req.url,'http://local').pathname.match(/^\/admin\/([a-z.-]+)?$/)?.[1]||'index.html';
 if(!/^[a-z.-]+$/.test(file)){res.writeHead(404);return res.end();}
 res.setHeader('Content-Type',file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html');
 res.end(fs.readFileSync(path.join(root,'apps/admin/static',file)));
});
(async()=>{
 await new Promise(r=>server.listen(0,'127.0.0.1',r));
 const browser=await chromium.launch({headless:true, executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000},reducedMotion:'reduce'}), errors=[],writes=[];
  page.setDefaultTimeout(10000);
  page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(()=>localStorage.setItem('ghbf_admin_token','mock-session'));
  await page.route('https://telegram.org/**',r=>r.fulfill({body:''}));
  const coupons=[],announcements=[];
  const inquiry={id:'inquiry-a',contact_method:'TELEGRAM',contact_handle:'@merchant',status:'NEW',created_at:'2026-09-21T10:00:00Z',configuration:{format:'combo',template_key:'digital-goods',product_source:'hybrid',delivery_model:'supabase_cloud',brief:{store_name:'North Store',visual_style:'Clean & minimal',store_language:'Arabic + English',report_language:'English',accent:'#166b52',requested_features:['coupons','warranty']},custom_api_request:'Connect our supplier'},estimated_quote:{total_one_time:'264.00',items:[{name:'Setup',item_type:'one_time',amount:'264.00'},{name:'Legacy monthly',item_type:'recurring',amount:'84.00'}]},project_notes:'Customer-owned Docker delivery'};
  await page.route('**/api/v1/**',async route=>{
   const req=route.request(),p=new URL(req.url()).pathname;let body={};
   if(p.startsWith('/api/v1/platform/')) assert.equal(req.headers().authorization,'Bearer mock-session');
   if(req.method()!=='GET')writes.push({p,body:req.postDataJSON()});
   if(p.endsWith('/admin/bootstrap'))body={store:{name:'North Store'},actor:{role:'OWNER',first_name:'Store owner'},counts:{products:12,active_products:10,orders:42,attention_orders:2,dead_letter_jobs:0,financial_open_cases:1,reconciliation_reviews:1}};
   else if(p.endsWith('/categories'))body=[];
   else if(p.endsWith('/onboarding/checklist'))body={items:[],progress_percent:100};
   else if(p.endsWith('/operations/cases'))body=[{id:'case-1',subject:'Gift code issue',kind:'WARRANTY',status:'OPEN',created_at:'2026-09-21'}];
   else if(p.endsWith('/operations/cases/case-1'))body={id:'case-1',subject:'Gift code issue',kind:'WARRANTY',status:'OPEN',version:1,warranty_terms:'30 day replacement after review',messages:[{is_staff:false,created_at:'2026-09-21',body:'My code does not work.'}]};
   else if(p.endsWith('/operations/coupons')){if(req.method()==='POST')coupons.push({id:'coupon-1',...req.postDataJSON(),used_count:0,is_active:true});body=req.method()==='POST'?coupons.at(-1):coupons;}
   else if(p.endsWith('/operations/announcements')){if(req.method()==='POST')announcements.push({id:'campaign-1',...req.postDataJSON(),status:'DRAFT',deliveries:{}});body=req.method()==='POST'?announcements.at(-1):announcements;}
   else if(p.endsWith('/announcements/campaign-1/queue')){announcements[0].status='QUEUED';body=announcements[0];}
   else if(p.endsWith('/bots/fleet'))body={bots:[{id:'bot-1',display_name:'North Store',is_enabled:true}]};
   else if(p.endsWith('/pricing-tiers')||p.endsWith('/pricing-rules'))body=[];
   else if(p.endsWith('/inquiries/inquiry-a'))body=inquiry;
   else if(p.endsWith('/inquiries'))body={items:[inquiry],total:1};
   else if(p.endsWith('/quotes')||p.endsWith('/handoffs'))body={items:[],total:0};
   return route.fulfill({json:body});
  });
  await page.goto(`http://127.0.0.1:${server.address().port}/admin/`);
  await page.locator('#app').waitFor({state:'visible'});
  await page.screenshot({path:'/tmp/ghbf-admin-home-desktop.png'});
  await page.locator('.nav[data-view="operations"]').click();
  await page.getByRole('button',{name:'View conversation'}).click();
  await page.locator('#operationsDialog').waitFor({state:'visible'});
  assert((await page.locator('#operationsFields').textContent()).includes('My code does not work.'));
  await page.locator('#operationsFields [name="body"]').fill('Approved after review; replacement recorded separately.');
  await page.locator('#operationsFields [name="status"]').selectOption('APPROVED');
  await page.locator('#saveOperations').click();
  await page.locator('#operationsDialog').waitFor({state:'hidden'});
  assert.equal(writes.at(-1).body.expected_version,1);
  await page.locator('[data-ops-tab="coupons"]').click();
  await page.getByRole('button',{name:'Create coupon'}).click();
  await page.locator('#operationsFields [name="code"]').fill('WELCOME');
  await page.locator('#operationsFields [name="expires_at"]').fill('2027-01-01T12:00');
  await page.locator('#saveOperations').click();
  await page.getByRole('heading',{name:'WELCOME',exact:true}).waitFor();
  await page.screenshot({path:'/tmp/ghbf-admin-operations-desktop.png'});
  await page.locator('[data-ops-tab="announcements"]').click();
  await page.getByRole('button',{name:'Write announcement'}).click();
  await page.locator('#operationsFields [name="title"]').fill('New arrivals');
  await page.locator('#operationsFields [name="body"]').fill('Explore our new gift cards.');
  await page.locator('#saveOperations').click();
  await page.getByRole('heading',{name:'New arrivals',exact:true}).waitFor();
  assert(!writes.some(w=>w.p.endsWith('/queue')));
  await page.getByRole('button',{name:'Review',exact:true}).click();
  await page.getByRole('button',{name:'Queue announcement'}).click();
  await page.locator('#operationsDialog').waitFor({state:'hidden'});
  assert(writes.some(w=>w.p.endsWith('/queue')));
  await page.locator('[data-ops-tab="pricing"]').click();
  await page.getByRole('button',{name:'Create margin rule'}).click();
  await page.locator('#operationsFields [name="name"]').fill('Retail markup');
  await page.locator('#saveOperations').click();
  await page.locator('#operationsDialog').waitFor({state:'hidden'});
  assert.equal(writes.at(-1).body.scope,'GLOBAL');
  await page.setViewportSize({width:390,height:844});
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await page.waitForFunction(()=>document.querySelector('.sidebar').getBoundingClientRect().right <= 0);
  await page.screenshot({path:'/tmp/ghbf-admin-operations-mobile.png'});
  await page.locator('#mobileMenuBtn').click();
  await page.waitForFunction(()=>document.querySelector('.sidebar').getBoundingClientRect().left >= 0);
  await page.locator('.nav[data-view="operations"]').click();
  await page.waitForFunction(()=>document.querySelector('.sidebar').getBoundingClientRect().right <= 0);
  await page.setViewportSize({width:1440,height:1000});
  await page.evaluate(()=>{return navigateTo('sales');});
  await page.locator('[data-inspect-inquiry]').click();
  await page.locator('#inquiryDetailDialog').waitFor({state:'visible'});
  assert((await page.locator('#inquiryDetailContent').textContent()).includes('North Store'));
  await page.screenshot({path:'/tmp/ghbf-admin-brief-desktop.png'});
  await page.locator('#openCreateQuoteFromInquiryBtn').click();
  assert.equal(await page.locator('.quote-line-row').count(),1);
  assert.equal(await page.locator('.quote-line-type option[value="recurring"]').count(),0);
  assert(!(await page.locator('#quoteTerms').inputValue()).includes('99.9%'));
  assert.deepEqual(errors,[]);
  console.log('Admin operations browser checks passed: care decision, coupon, draft/review/send, margin rule, inquiry scope, one-time quote, desktop/mobile layout.');
 }finally{await browser.close();server.close();}
})().catch(e=>{console.error(e);server.close();process.exitCode=1;});
