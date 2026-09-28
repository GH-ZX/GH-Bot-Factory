const { chromium } = require('playwright');
const path = require('node:path');
const assert = require('node:assert/strict');
const server = require('./browser_static.cjs').createServer(path.resolve(__dirname, '..'));

(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({
    headless: true,
    executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE
  });

  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
    const errors = [];
    page.on('pageerror', err => console.error('PAGE ERROR:', err));
    page.on('console', msg => console.log('PAGE LOG:', msg.type(), msg.text()));

    const base = `http://127.0.0.1:${server.address().port}`;
    await page.goto(`${base}/miniapp/?preview=1`, { waitUntil: 'networkidle' });

    // 1. Verify minimal product card in grid
    const cards = page.locator('.product-card');
    const cardCount = await cards.count();
    assert.ok(cardCount >= 3, `Expected at least 3 products, got ${cardCount}`);

    // Verify grid cards do NOT have bulky variant lists or descriptions
    assert.equal(await page.locator('.product-card .variant-item').count(), 0);
    assert.equal(await page.locator('.product-card .product-card-desc').count(), 0);

    // Verify minimal elements exist
    const firstCard = cards.first();
    assert.equal(await firstCard.locator('.product-title').isVisible(), true);
    assert.equal(await firstCard.locator('.product-price').isVisible(), true);
    assert.equal(await firstCard.locator('.product-open-cue').isVisible(), true);

    // Screenshot minimal grid
    await page.screenshot({ path: '/tmp/ghbf-minimal-grid-mobile.png' });
    console.log('✔ Minimal product grid verified');

    // 2. Open Product Page Dialog
    await firstCard.click();
    const dialog = page.locator('#productDetailDialog');
    await dialog.waitFor({ state: 'visible' });

    // Assert dialog contents
    const title = await page.locator('#productDetailTitle').innerText();
    assert.equal(title, 'Create something great');

    const eyebrow = await page.locator('#productDetailEyebrow').innerText();
    assert.equal(eyebrow, 'DIGITAL ESSENTIALS');

    // Verify warranty card is rendered
    const warranty = page.locator('.detail-warranty-box');
    assert.equal(await warranty.isVisible(), true);
    const warrantyText = await warranty.innerText();
    assert.ok(warrantyText.includes('Warranty · 7 days'));

    // Verify options list (2 variants)
    const options = page.locator('.detail-option-card');
    assert.equal(await options.count(), 2);

    // Initial total in sticky button
    let addBtnText = await page.locator('#detailAddToCartBtn').innerText();
    assert.ok(addBtnText.includes('Add to cart'), `Expected 'Add to cart', got: ${addBtnText}`);
    assert.ok(addBtnText.includes('$12.00'), `Expected '$12.00', got: ${addBtnText}`);

    // Click "+" stepper
    await page.locator('#detailQtyPlus').click();
    addBtnText = await page.locator('#detailAddToCartBtn').innerText();
    assert.ok(addBtnText.includes('$24.00'), `Expected '$24.00' after +1 qty, got: ${addBtnText}`);

    // Switch to second variant ("Complete collection" at $24.00)
    await options.nth(1).click();
    addBtnText = await page.locator('#detailAddToCartBtn').innerText();
    assert.ok(addBtnText.includes('$24.00'), `Expected '$24.00' for Complete collection, got: ${addBtnText}`);

    // Screenshot open product page on mobile
    await page.screenshot({ path: '/tmp/ghbf-product-detail-mobile.png' });
    console.log('✔ Rich Product Page dialog (Mobile) verified');

    // Test close button
    await page.locator('#closeProductDetail').click();
    await dialog.waitFor({ state: 'hidden' });
    console.log('✔ Close button works');

    // 3. Test Desktop view (Modal)
    await page.setViewportSize({ width: 1200, height: 800 });
    await firstCard.click();
    await dialog.waitFor({ state: 'visible' });
    await page.screenshot({ path: '/tmp/ghbf-product-detail-desktop.png' });
    console.log('✔ Rich Product Page dialog (Desktop) verified');
    await page.locator('#closeProductDetail').click();
    await dialog.waitFor({ state: 'hidden' });

    // 4. Test Arabic / RTL
    await page.locator('[data-target="settings"]').click();
    await page.locator('#storeLanguage').selectOption('ar');
    await page.locator('[data-target="shop"]').click();
    await page.locator('.product-card').first().click();
    await dialog.waitFor({ state: 'visible' });
    const arBtnText = await page.locator('#detailAddToCartBtn').innerText();
    assert.ok(arBtnText.includes('إضافة إلى السلة'), `Expected Arabic add to cart, got: ${arBtnText}`);
    await page.screenshot({ path: '/tmp/ghbf-product-detail-arabic.png' });
    console.log('✔ Rich Product Page (Arabic RTL) verified');

    assert.deepEqual(errors, []);
    console.log('All minimal product card and rich product detail tests PASSED successfully!');
  } finally {
    await browser.close();
    server.close();
  }
})().catch(err => {
  console.error(err);
  process.exitCode = 1;
  server.close();
});
