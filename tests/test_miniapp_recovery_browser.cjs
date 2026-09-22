const {chromium}=require('playwright');
const path=require('node:path'),assert=require('node:assert/strict');
const server=require('./browser_static.cjs').createServer(path.resolve(__dirname,'..'));
(async()=>{
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const browser=await chromium.launch({headless:true,executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE});
  try{
    const page=await browser.newPage();
    await page.route('https://telegram.org/**',route=>route.fulfill({body:''}));
    await page.goto(`http://127.0.0.1:${server.address().port}/miniapp/?preview=1`);
    await page.locator('#authenticatedApp').waitFor({state:'visible'});
    const result=await page.evaluate(async()=>{
      const {pendingOperations}=await import('/miniapp/pending-operations.js');
      const state={bootstrap:{store:{id:'isolation-a'},user:{id:'buyer-a'}}};
      const calls=[];let fail=true;
      const api=async(path,options)=>{calls.push(JSON.parse(options.body));if(fail)throw new Error('Connection lost after request');return {id:'existing-order'};};
      const create=()=>pendingOperations({state,api,t:s=>s,onRecovered:async()=>{}});
      const first=create(),payload={items:[{variant_id:'one',quantity:1}],recipient:'buyer',idempotency_key:'original-request'};
      try{await first.run('/api/v1/storefront/checkout',payload);}catch(_){}
      const visible=!document.getElementById('pendingOperations').classList.contains('hidden');
      const resumed=create();let blocked=false;
      try{await resumed.run('/api/v1/storefront/checkout',{...payload,recipient:'different',idempotency_key:'new-request'});}catch(_){blocked=true;}
      const callsBeforeRecovery=calls.length;
      fail=false;
      const recovered=await resumed.run('/api/v1/storefront/checkout',{...payload,idempotency_key:'new-request'});
      return {visible,blocked,callsBeforeRecovery,calls,recovered,cleared:document.getElementById('pendingOperations').classList.contains('hidden')};
    });
    assert.equal(result.visible,true);
    assert.equal(result.blocked,true);
    assert.equal(result.callsBeforeRecovery,1);
    assert.equal(result.calls[1].idempotency_key,'original-request');
    assert.equal(result.recovered.id,'existing-order');
    assert.equal(result.cleared,true);
    console.log('MiniApp lost-response recovery preserves payload/key and blocks changed purchase');
  }finally{await browser.close();server.close();}
})().catch(error=>{console.error(error);process.exitCode=1;server.close();});
