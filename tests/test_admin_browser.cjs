/* Run with NODE_PATH pointing to an installed Playwright package. No live API required. */
const { chromium } = require('playwright');
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '..');
const server = http.createServer((req, res) => {
  const url = new URL(req.url, 'http://localhost');
  const match = url.pathname.match(/^\/(admin|miniapp)\/(index.html|app.js|styles.css|store-setup.js|store-setup.css)?$/);
  if (!match) { res.writeHead(404); res.end(); return; }
  const file = match[2] || 'index.html';
  res.setHeader('Content-Type', file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html');
  res.end(fs.readFileSync(path.join(root, 'apps', match[1], 'static', file)));
});
(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({headless: true, executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined});
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('https://telegram.org/**', route => route.fulfill({body: ''}));
    let loginBody;
    let allowLogin = false;
    await page.route('**/api/v1/**', route => {
      const url = new URL(route.request().url());
      let body = {};
      if (url.pathname.endsWith('/admin-code')) {
        loginBody = route.request().postDataJSON();
        return route.fulfill({status: allowLogin ? 200 : 401, json: allowLogin ? {access_token: 'browser-test-session'} : {detail: 'Code expired. Send /admin for a new code.'}});
      }
      if (url.pathname.endsWith('/admin/bootstrap')) body = {store: {name: 'Test Store'}, actor: {role: 'OWNER', first_name: 'Operator'}, counts: {products: 0, active_products: 0, orders: 0, attention_orders: 0, dead_letter_jobs: 0, financial_open_cases: 0, reconciliation_reviews: 0}};
      else if (url.pathname.endsWith('/categories')) body = [];
      return route.fulfill({json: body});
    });
    const base = `http://127.0.0.1:${server.address().port}`;
    await page.goto(`${base}/admin/`);
    await page.locator('#login').waitFor({state: 'visible'});
    assert.equal(await page.locator('#fatal').isVisible(), false);
    await page.locator('#loginCode').fill('a'.repeat(32));
    await page.locator('#loginSubmit').click();
    await page.waitForFunction(() => document.getElementById('loginError').textContent.includes('expired'));
    assert.equal(await page.locator('#loginCode').inputValue(), '');
    allowLogin = true;
    await page.locator('#loginCode').fill('b'.repeat(32));
    await page.locator('#loginSubmit').click();
    await page.locator('#app').waitFor({state: 'visible'});
    assert.deepEqual(loginBody, {code: 'b'.repeat(32)});
    assert.equal(await page.evaluate(() => localStorage.getItem('ghbf_admin_token')), 'browser-test-session');
    await page.reload();
    await page.locator('#app').waitFor({state: 'visible'});
    await page.locator('#signOut').click();
    await page.locator('#login').waitFor({state: 'visible'});
    assert.equal(await page.evaluate(() => localStorage.getItem('ghbf_admin_token')), null);
    await page.setViewportSize({width: 390, height: 844});
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await page.screenshot({path: '/tmp/ghbf-admin-login-mobile.png'});
    assert.deepEqual(errors, []);
    console.log('Admin browser checks passed: direct entry, invalid code recovery, login, reload persistence, logout, mobile layout.');
    const store = await browser.newPage({viewport: {width: 390, height: 844}});
    store.on('pageerror', error => errors.push(error.message));
    await store.addInitScript(() => {
      window.Telegram = {WebApp: {initData: 'verified-by-test-api', ready() {}, expand() {}}};
    });
    await store.route('https://telegram.org/**', route => route.fulfill({body: ''}));
    const paymentRequests = [];
    const method = {id: 'method-1', display_name: 'Bank transfer', currencies: ['USD'], min_amount: '1', max_amount: '1000', verification_mode: 'MANUAL'};
    const flexibleMethod = {...method, id: 'method-2', display_name: 'Asset deposit', flexible_deposits_enabled: true, auto_credit_enabled: true};
    await store.route('**/api/v1/**', route => {
      const request = route.request();
      const url = new URL(request.url());
      const pathname = url.pathname;
      let body = {};
      if (pathname.endsWith('/telegram-miniapp')) body = {access_token: 'store-test'};
      else if (pathname.endsWith('/bootstrap')) body = {store: {id: 'tenant', name: 'Store', settings: {}}, user: {id: 'user', first_name: 'Buyer'}, wallets: [{currency: 'USD', balance: '20'}], asset_wallets: [{asset: 'USDT', network: 'TRON', balance: '1.000000000000000001'}]};
      else if (pathname.endsWith('/catalog')) body = {categories: [], products: [], total: 0, has_more: false};
      else if (pathname.endsWith('/orders')) body = [];
      else if (pathname.endsWith('/topups/options')) body = {providers: []};
      else if (pathname.endsWith('/payment-methods')) body = {methods: [method, flexibleMethod]};
      else if (pathname.endsWith('/topups/method')) {
        paymentRequests.push(request.postDataJSON());
        body = {id: 'intent-1', status: 'PENDING', amount: '10', currency: 'USD', payment_method_id: 'method-1', payment_instructions: {mode: 'local', verification_mode: 'MANUAL', instructions: 'Use reference INTENT-1', destination_address: 'Bank account 123'}};
      } else if (pathname.endsWith('/observations')) { paymentRequests.push(request.postDataJSON()); body = {status: 'MANUAL_REVIEW'}; }
      else if (pathname.endsWith('/topups/intent-1/reconcile')) body = {id: 'intent-1', status: 'SUCCEEDED', amount: '10', currency: 'USD', wallet_balance: '30'};
      else if (pathname.endsWith('/flexible-deposits')) { paymentRequests.push(request.postDataJSON()); body = {id: 'deposit-1', status: 'PENDING', amount_received: null, asset: null, network: null}; }
      else if (pathname.endsWith('/flexible-deposits/deposit-1/reconcile')) body = {id: 'deposit-1', status: 'CREDITED', amount_received: '7.123456789', credited_amount: '7.123456789', credited_asset: 'USDT', network: 'TRON'};
      return route.fulfill({json: body});
    });
    await store.goto(`${base}/miniapp/?bot_id=11111111-1111-1111-1111-111111111111`);
    await store.locator('#authenticatedApp').waitFor({state: 'visible'});
    await store.locator('[data-target="account"]').click();
    assert((await store.locator('#walletGrid').textContent()).includes('1.000000000000000001'));
    await store.locator('#fundWalletButton').click();
    await store.locator('#topupAmountInput').fill('10');
    await store.locator('#topupSubmitButton').click();
    await store.locator('#topupProofForm').waitFor({state: 'visible'});
    assert.equal(paymentRequests[0].payment_method_id, 'method-1');
    assert.equal(paymentRequests[0].provider_name, undefined);
    assert((await store.locator('#topupInstructions').textContent()).includes('Bank account 123'));
    await store.locator('#topupProofReference').fill('transfer-reference');
    await store.locator('#topupProofSubmit').click();
    await store.waitForFunction(() => !document.getElementById('topupProofSubmit').disabled);
    assert.equal(paymentRequests[1].source, 'MANUAL');
    await store.locator('#topupCheckButton').click();
    await store.waitForFunction(() => document.getElementById('topupStatusLabel').textContent === 'SUCCEEDED');
    await store.locator('#closeTopupButton').click();
    await store.locator('#fundWalletButton').click();
    await store.locator('#topupProviderSelect').selectOption('method-2');
    assert.equal(await store.locator('#topupFixedFields').isVisible(), false);
    await store.locator('#topupSubmitButton').click();
    await store.waitForFunction(() => document.getElementById('topupStatusLabel').textContent === 'PENDING');
    assert.equal(paymentRequests[2].payment_method_id, 'method-2');
    assert.equal(paymentRequests[2].amount, undefined);
    await store.locator('#topupCheckButton').click();
    await store.waitForFunction(() => document.getElementById('topupStatusLabel').textContent === 'CREDITED');
    assert((await store.locator('#topupStatusAmount').textContent()).includes('7.123456789 USDT'));
    assert.deepEqual(errors, []);
    console.log('Mini App browser checks passed: method-based top-up, manual reference, flexible deposit, exact asset display.');

  } finally { await browser.close(); server.close(); }
})().catch(error => { console.error(error); server.close(); process.exitCode = 1; });
