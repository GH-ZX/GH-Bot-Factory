/* Browser regression: choices, draft recovery, failed requests, and mobile setup. */
const {chromium}=require('playwright');
const fs=require('node:fs');
const http=require('node:http');
const path=require('node:path');
const {execFileSync}=require('node:child_process');
const assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..');
const templates=JSON.parse(execFileSync(path.join(root,'.venv/bin/python'),['-c','import json; from packages.factory.templates import list_bot_templates; print(json.dumps([t.public_payload() for t in list_bot_templates()]))'],{cwd:root,encoding:'utf8'}));
const server=http.createServer((req,res)=>{
  const match=new URL(req.url,'http://localhost').pathname.match(/^\/(admin|build)\/([a-z.-]+)?$/);
  if(!match){res.writeHead(404);return res.end();}
  const file=path.join(root,'apps',match[1]==='build'?'configurator':'admin','static',match[2]||'index.html');
  if(!fs.existsSync(file)){res.writeHead(404);return res.end();}
  res.setHeader('Content-Type',file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html');res.end(fs.readFileSync(file));
});
(async()=>{
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const browser=await chromium.launch({headless:true,executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE||undefined});
  try{
    const page=await browser.newPage({viewport:{width:1280,height:900},reducedMotion:'reduce'});
    page.setDefaultTimeout(10000);
    const errors=[];page.on('pageerror',e=>errors.push(e.message));
    await page.route('https://telegram.org/**',r=>r.fulfill({body:''}));
    let failEstimate=false,failInquiry=true,failCreate=true,createPayload=null,inquiryPayload=null;
    let storeId='tenant-a';
    await page.route('**/api/v1/**',async route=>{
      const req=route.request(),url=new URL(req.url()),p=url.pathname;
      let body={};
      if(p.endsWith('/templates'))body={templates};
      else if(p.endsWith('/public/integrations'))body={integrations:[]};
      else if(p.endsWith('/public/estimate')){
        if(failEstimate)return route.fulfill({status:503,json:{detail:'Please retry shortly.'}});
        const v=req.postDataJSON();body={...v,template_name:templates.find(t=>t.key===v.template_key).name,selected_integrations:v.integration_keys,total_one_time:'89.00',total_monthly:'49.00',items:[],notes:'Example estimate',telegram_contact_url:null};
      }else if(p.endsWith('/public/inquiries')){
        inquiryPayload=req.postDataJSON();
        if(failInquiry)return route.fulfill({status:503,json:{detail:'Could not send. Try again.'}});
        body={inquiry_id:'12345678-demo',telegram_link:null};
      }else if(p.endsWith('/admin/bootstrap'))body={store:{id:storeId,name:'Demo store'},actor:{id:'owner',role:'OWNER'},counts:{products:0,active_products:0,orders:0,attention_orders:0,dead_letter_jobs:0,financial_open_cases:0,reconciliation_reviews:0}};
      else if(p.endsWith('/categories'))body=[];
      else if(p.endsWith('/onboarding/checklist'))body={progress_percent:0,launch_ready:false,recommended_template_key:'general-commerce',items:[{key:'bot_token',title:'Connect Telegram',description:'Connect your bot.',action_view:'bots',completed:false},{key:'catalog',title:'Products',description:'Add stock.',action_view:'products',completed:false}]};
      else if(p.endsWith('/saas/overview'))body={usage:{},limits:{},features:{},commercial_access:{allowed:true}};
      else if(p.endsWith('/saas/billing'))body={policy:{past_due_grace_days:0},catalog:[]};
      else if(p.endsWith('/bots/fleet'))body={bots:[]};
      else if(p.endsWith('/bots/jobs'))body={jobs:[]};
      else if(p.endsWith('/bots/wizard/options')){
        const t=templates.find(t=>t.key===url.searchParams.get('template_key'));
        body={business_type:t.business_type,routing_strategies:['PRIORITY','AVAILABILITY','LOWEST_COST','MANUAL'],default_routing_strategy:t.default_routing_strategy,pricing_tiers:[],providers:[],payment_methods:[]};
      }else if(p.endsWith('/bots/provision')){
        createPayload=req.postDataJSON();
        if(failCreate)return route.fulfill({status:422,json:{detail:'Telegram could not verify this token. Check it in BotFather.'}});
        body={id:'job-example'};
      }else return route.fulfill({status:404,json:{detail:`Unexpected fixture route ${p}`}});
      return route.fulfill({json:body});
    });
    const base=`http://127.0.0.1:${server.address().port}`;
    await page.goto(`${base}/build/`);
    await page.locator('#templatesGrid .setup-category').first().waitFor();
    assert.equal(await page.locator('#templatesGrid .setup-category').count(),5);
    await page.locator('[data-group="gifts"]').click();
    await page.locator('#templatesGrid [data-source]').selectOption('provider_api');
    await page.waitForFunction(()=>state.currentEstimate?.template_key==='gift-reseller');
    await page.locator('#previewName').fill('My Cards');
    await page.locator('#storeDemo .setup-demo-next').click();
    await page.locator('#storeDemo .setup-demo-next').click();
    assert.match(await page.locator('#storeDemo').innerText(),/Preview complete/);
    await page.locator('#storeDemo [data-mode="chat"]').click();
    assert.match(await page.locator('#storeDemo').innerText(),/Bot conversation/);
    await page.reload();
    await page.waitForFunction(()=>state.currentEstimate?.template_key==='gift-reseller');
    assert.equal(await page.locator('#previewName').inputValue(),'My Cards');
    failEstimate=true;
    await page.locator('#templatesGrid [data-source]').selectOption('stored');
    await page.locator('#estimateError').filter({hasText:'Could not update'}).waitFor();
    assert.equal(await page.locator('#estOneTime').innerText(),'Unavailable');
    failEstimate=false;
    await page.locator('#templatesGrid [data-source]').selectOption('provider_api');
    await page.waitForFunction(()=>state.currentEstimate!==null);
    await page.locator('#contactHandle').fill('@example');
    await page.locator('#projectNotes').fill('Please keep my notes.');
    await page.locator('#submitInquiryBtn').click();
    await page.locator('#inquiryStatusMsg.error').waitFor();
    assert.equal(await page.locator('#projectNotes').inputValue(),'Please keep my notes.');
    failInquiry=false;
    await page.locator('#submitInquiryBtn').click();
    await page.locator('#inquiryStatusMsg.success').waitFor();
    assert.match(inquiryPayload.project_notes,/My Cards/);
    assert.equal(await page.evaluate(()=>sessionStorage.getItem('ghbf_build_choices_v1')),null);
    fs.mkdirSync(path.join(root,'artifacts'),{recursive:true});
    await page.evaluate(()=>window.scrollTo(0,0));
    await page.screenshot({path:path.join(root,'artifacts/setup-public-desktop.png'),fullPage:true});
    for(const viewport of [{width:375,height:812},{width:812,height:375}]){
      await page.setViewportSize(viewport);
      assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'Public page overflows');
    }
    await page.setViewportSize({width:375,height:812});
    await page.evaluate(()=>window.scrollTo(0,0));
    await page.screenshot({path:path.join(root,'artifacts/setup-public-mobile.png'),fullPage:true});
    await page.evaluate(()=>localStorage.setItem('ghbf_admin_token','demo-session'));
    await page.goto(`${base}/admin/`);
    await page.locator('[data-onboarding-key="bot_token"]').click();
    await page.locator('#botProvisionDialog[open]').waitFor();
    assert.equal(await page.locator('#botBusinessChooser .setup-category').count(),5);
    await page.locator('#botWizardNext').click();
    await page.locator('#botWizardNext').click();
    assert.match(await page.locator('#botWizardError').innerText(),/BotFather token/);
    await page.locator('#botToken').fill('example-private-value');
    await page.locator('#botWizardNext').click();
    await page.locator('#botDisplayName').fill('Keep this name');
    await page.locator('#botWizardBack').click();await page.locator('#botWizardBack').click();
    await page.locator('#botBusinessChooser [data-group="gifts"]').click();
    await page.waitForFunction(()=>botOptionsReady);
    assert.equal(await page.locator('#botDisplayName').inputValue(),'Keep this name');
    await page.getByRole('button',{name:'Continue later',exact:true}).click();
    await page.waitForFunction(()=>document.getElementById('botToken').value==='');
    const drafts=await page.evaluate(()=>JSON.stringify(sessionStorage));
    assert(!drafts.includes('example-private-value'),'Credential leaked into draft');
    await page.locator('#newBot').click();
    await page.locator('#resumeBotDraft').click();
    assert.equal(await page.locator('#botDisplayName').inputValue(),'Keep this name');
    assert.equal(await page.locator('#botToken').inputValue(),'');
    await page.locator('#botWizardNext').click();
    await page.locator('#botToken').fill('example-private-value');
    await page.locator('#botWizardNext').click();await page.locator('#botWizardNext').click();
    assert.equal(await page.locator('#botAdvancedConnections').getAttribute('open'),null);
    await page.locator('#botWizardNext').click();
    await page.locator('#botWizardSubmit').click();
    await page.locator('#botWizardError').filter({hasText:'Telegram could not'}).waitFor();
    assert.equal(await page.locator('#botDisplayName').inputValue(),'Keep this name');
    assert.equal(await page.locator('#botToken').inputValue(),'example-private-value');
    await page.setViewportSize({width:375,height:812});
    await page.screenshot({path:path.join(root,'artifacts/setup-admin-mobile.png'),fullPage:true});
    assert(await page.evaluate(()=>document.getElementById('botProvisionDialog').getBoundingClientRect().right<=innerWidth),'Wizard overflows');
    failCreate=false;
    await page.locator('#botWizardSubmit').click();
    await page.waitForFunction(()=>!document.getElementById('botProvisionDialog').open);
    assert.equal(createPayload.template_key,'gift-cards');
    assert.equal(createPayload.display_name,'Keep this name');
    assert.equal(await page.evaluate(()=>sessionStorage.getItem('ghbf_bot_draft:tenant-a:owner:new')),null);
    // A draft for another store must never be offered to this store.
    await page.evaluate(()=>StoreSetup.saveDraft('ghbf_bot_draft:tenant-a:owner:new',{template:'general-commerce',fields:{botDisplayName:'Tenant A only'}}));
    storeId='tenant-b';await page.reload();
    await page.locator('[data-onboarding-key="bot_token"]').click();
    await page.locator('#botProvisionDialog[open]').waitFor();
    assert.equal(await page.locator('#resumeBotDraft').isVisible(),false);
    await page.getByRole('button',{name:'Continue later',exact:true}).click();
    await page.setViewportSize({width:1280,height:900});
    await page.locator('.nav[data-view="dashboard"]').click();
    await page.screenshot({path:path.join(root,'artifacts/setup-admin-desktop.png'),fullPage:true});
    await page.locator('#homePreview').click();
    await page.locator('#homePreviewDialog[open]').waitFor();
    assert.equal(await page.locator('#previewLaunchCheck').isDisabled(),true);
    await page.locator('#homeCustomerDemo .setup-demo-next').click();
    assert.match(await page.locator('#homeCustomerDemo').innerText(),/Example price/);
    assert.deepEqual(errors,[]);
    console.log('Setup UX browser regression passed: categories, preview, drafts, tenant separation, failed requests, mobile layouts, and launch guidance.');
  }finally{await browser.close();server.close();}
})().catch(error=>{console.error(error);process.exitCode=1;server.close();});
