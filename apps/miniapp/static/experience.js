export function bindExperience({state,api,esc,money,t,format,toast,openPayment,addToCart,openCart}){
  const el=id=>document.getElementById(id);
  let historyOffset=null,historyBusy=false,historyRevision=0;

  const VOUCHER_REDEMPTION_GUIDES = {
    pubg: {
      url: "https://www.midasbuy.com",
      steps_en: [
        "Visit www.midasbuy.com and select PUBG Mobile.",
        "Log in and enter your Player ID to verify your character nickname.",
        "Enter the voucher PIN code received in your order.",
        "Click OK to redeem. Your UC will be credited to your game account immediately."
      ],
      steps_ar: [
        "توجه إلى موقع midasbuy.com الرسمي واختر لعبة ببجي موبايل.",
        "سجل الدخول وأدخل معرف اللاعب (Player ID) وتأكد من ظهور اسم حسابك.",
        "أدخل كود القسيمة (PIN) المستلم من تفاصيل طلبك.",
        "اضغط تأكيد، وسيتم شحن الشدات (UC) في حسابك داخل اللعبة فوراً."
      ]
    },
    free_fire: {
      url: "https://shop2game.com",
      steps_en: [
        "Visit shop2game.com and select Free Fire.",
        "Log in using your Player ID.",
        "Choose Garena Voucher as payment method and enter your voucher PIN.",
        "Confirm redemption to receive your diamonds in-game instantly."
      ],
      steps_ar: [
        "توجه إلى موقع shop2game.com واختر فري فاير.",
        "سجل الدخول بواسطة معرف اللاعب (Player ID) الخاص بك.",
        "اختر طريقة الدفع عبر بطاقة قارينا وأدخل كود القسيمة المستلم.",
        "اضغط تأكيد لإضافة الجواهر فوراً إلى حسابك في اللعبة."
      ]
    },
    razer: {
      url: "https://gold.razer.com",
      steps_en: [
        "Log in to your Razer Gold account at gold.razer.com.",
        "Click 'Reload Now' and select 'Razer Gold PIN'.",
        "Enter the PIN code received and follow instructions to add funds.",
        "Use your balance to purchase gaming credits across thousands of games."
      ],
      steps_ar: [
        "سجل الدخول إلى حسابك في موقع gold.razer.com.",
        "اضغط على زر 'Reload Now' (إعادة الشحن) واختر 'Razer Gold PIN'.",
        "أدخل رمز PIN المستلم واضغط متابعة لإضافة الرصيد إلى محفظتك.",
        "استخدم رصيد المحفظة للشحن في آلاف الألعاب والتطبيقات المدعومة."
      ]
    },
    steam: {
      url: "https://store.steampowered.com/account/redeemwalletcode",
      steps_en: [
        "Launch the Steam client or go to store.steampowered.com/account/redeemwalletcode.",
        "Sign in to your Steam account.",
        "Enter the Steam Wallet Code into the input field.",
        "Click Continue. The funds will be added to your Steam Wallet immediately."
      ],
      steps_ar: [
        "افتح تطبيق Steam أو توجه إلى store.steampowered.com/account/redeemwalletcode.",
        "سجل الدخول إلى حساب ستيم الخاص بك.",
        "أدخل كود محفظة ستيم في خانة الرمز.",
        "اضغط متابعة (Continue) لإضافة الرصيد إلى محفظتك واستخدامه لشراء الألعاب."
      ]
    },
    playstation: {
      url: "https://store.playstation.com",
      steps_en: [
        "Open PlayStation Store on your console or visit store.playstation.com.",
        "Sign in to your PlayStation Network account.",
        "Click on your profile avatar at the top and select 'Redeem Code'.",
        "Enter the 12-digit voucher code and select Redeem."
      ],
      steps_ar: [
        "افتح متجر PlayStation Store من جهاز الكونسول أو المتصفح.",
        "سجل الدخول إلى حساب شبكة PlayStation Network الخاص بك.",
        "اضغط على صورة ملفك الشخصي بالأعلى واختر 'Redeem Code' (استرداد الرمز).",
        "أدخل رمز القسيمة المكون من 12 خانة واضغط Redeem لتعبئة المحفظة."
      ]
    },
    xbox: {
      url: "https://redeem.microsoft.com",
      steps_en: [
        "Go to redeem.microsoft.com in any web browser.",
        "Sign in with your Microsoft / Xbox account.",
        "Enter the 25-character code received.",
        "Click Next and confirm to apply the gift card or subscription."
      ],
      steps_ar: [
        "توجه إلى موقع redeem.microsoft.com عبر المتصفح.",
        "سجل الدخول بحساب Microsoft / Xbox الخاص بك.",
        "أدخل الكود المكون من 25 رمزاً في الخانة المخصصة.",
        "اضغط التالي (Next) وتأكيد لتفعيل الرصيد أو الاشتراك في حسابك."
      ]
    },
    roblox: {
      url: "https://www.roblox.com/redeem",
      steps_en: [
        "Go to roblox.com/redeem in your web browser.",
        "Log in to the Roblox account where you want the credit.",
        "Enter the PIN code from your order.",
        "Click Redeem to add Robux or Credit to your account."
      ],
      steps_ar: [
        "توجه إلى موقع roblox.com/redeem عبر المتصفح.",
        "سجل الدخول إلى حساب روبلوكس المراد شحنه.",
        "أدخل كود PIN المستلم من تفاصيل طلبك.",
        "اضغط Redeem لإضافة رصيد Robux أو رصيد المحفظة إلى حسابك فوراً."
      ]
    },
    valorant: {
      url: "https://playvalorant.com",
      steps_en: [
        "Launch the Valorant game client and log in.",
        "Click the VP icon in the top-right corner next to the Store tab.",
        "Select 'Prepaid Cards & Codes' payment method.",
        "Enter the voucher code and click Submit to receive your VP."
      ],
      steps_ar: [
        "افتح لعبة فالورانت وسجل الدخول إلى حسابك.",
        "اضغط على أيقونة نقاط VP في الزاوية العلوية بجوار تبويب المتجر.",
        "اختر طريقة الدفع 'Prepaid Cards & Codes' (البطاقات مسبقة الدفع).",
        "أدخل كود القسيمة واضغط Submit لاستلام النقاط فوراً داخل اللعبة."
      ]
    }
  };

  const GENERIC_VOUCHER_GUIDE = {
    url: "",
    steps_en: [
      "Visit the official redemption website or launch the application.",
      "Sign in to your account.",
      "Navigate to 'Redeem Code' or 'Prepaid Voucher' section.",
      "Enter the digital voucher code and confirm to activate."
    ],
    steps_ar: [
      "توجه إلى الموقع الرسمي أو افتح التطبيق التابع للخدمة.",
      "سجل الدخول إلى حسابك الشخصي.",
      "انتقل إلى قسم 'استرداد الرمز' أو 'شحن بطاقة مسبقة الدفع'.",
      "أدخل كود القسيمة الرقمية واضغط تأكيد للتفعيل فوراً."
    ]
  };

  function resolveVoucherGuide(productTitle, categoryName, metadata = {}) {
    if (metadata.instructions_en || metadata.instructions_ar) {
      return {
        url: metadata.redemption_url || "",
        steps_en: metadata.instructions_en || [],
        steps_ar: metadata.instructions_ar || [],
      };
    }
    const combined = `${productTitle} ${categoryName || ""}`.toLowerCase();
    for (const [key, guide] of Object.entries(VOUCHER_REDEMPTION_GUIDES)) {
      if (combined.includes(key.replace("_", " ")) || combined.includes(key)) {
        return guide;
      }
    }
    const voucherKeywords = ["voucher", "قسيمة", "بطاقة", "gift card", "redeem", "code", "pin", "شحن شدات", "شحن جواهر", "كود"];
    if (voucherKeywords.some(kw => combined.includes(kw))) {
      return GENERIC_VOUCHER_GUIDE;
    }
    return null;
  }

  function getProductIcon(title) {
    const lower = (title || "").toLowerCase();
    if (lower.includes("pubg") || lower.includes("free fire") || lower.includes("game") || lower.includes("razer") || lower.includes("steam") || lower.includes("uc ") || lower.includes("coins")) return "🎮";
    if (lower.includes("chatgpt") || lower.includes("gemini") || lower.includes("claude") || lower.includes("ai ") || lower.includes("bot") || lower.includes("duolingo")) return "🤖";
    if (lower.includes("itunes") || lower.includes("card") || lower.includes("gift") || lower.includes("voucher") || lower.includes("apple")) return "💳";
    if (lower.includes("vpn") || lower.includes("proxy") || lower.includes("account") || lower.includes("sub") || lower.includes("plus") || lower.includes("premium")) return "💎";
    if (lower.includes("sms") || lower.includes("number") || lower.includes("sim") || lower.includes("phone")) return "📱";
    return "⚡";
  }

  function renderHelp(){
    const settings=state.bootstrap?.store?.settings||{};
    el('salesPauseBanner').textContent=t('New purchases are temporarily paused. Existing orders and support remain available.');
    el('salesPauseBanner').classList.toggle('hidden',settings.sales_paused!==true);
    el('storeNotice').textContent=settings.store_notice||'';
    el('storeNotice').classList.toggle('hidden',!settings.store_notice);
    el('helpStoreDescription').textContent=settings.store_description||'';
    el('storeFAQ').innerHTML=(Array.isArray(settings.faq)?settings.faq:[]).slice(0,12).map(item=>`<details class="faq-item"><summary>${esc(item.question)}</summary><p>${esc(item.answer)}</p></details>`).join('');
    const safe=value=>{try{const u=new URL(value);return u.protocol==='https:'&&!u.username&&!u.password?u.href:null;}catch(_){}return null;};
    el('storePolicyLinks').innerHTML=[['support_url','Contact the store'],['terms_url','Terms'],['privacy_url','Privacy']].map(([key,label])=>{const url=safe(settings[key]);return url?`<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${t(label)} ↗</a>`:'';}).join('');
    document.querySelector('label[for="recipientInput"]').textContent=settings.recipient_label||t('Delivery recipient');
    el('recipientHelp').textContent=settings.recipient_help||t('Enter the destination required by the product provider.');
  }

  function product(id){
    const item=state.catalog.products.find(p=>p.id===id);if(!item)return;
    const cat = (state.catalog.categories||[]).find(c=>c.id===item.category_id);
    const categoryName = cat ? cat.name : t('Product');

    const image=item.metadata?.image_url||item.metadata?.thumbnail_url;
    let safeImage='';
    try{
      const url=new URL(image,location.origin);
      if(image&&url.protocol==='https:'&&!url.username&&!url.password)safeImage=url.href;
    }catch(_){}

    const icon = getProductIcon(item.title);
    const initial = (item.title||'').charAt(0).toUpperCase();

    const variants = item.variants || [];
    let selectedVariant = variants[0] || null;
    let currentQty = 1;

    const eyebrow = el('productDetailEyebrow');
    if (eyebrow) eyebrow.textContent = categoryName.toUpperCase();
    el('productDetailTitle').textContent = item.title;

    function renderDetailContent() {
      if (!selectedVariant) return;
      const inStock = Number(selectedVariant.stock_quantity) > 0;
      const stockBadge = inStock
        ? `<span class="detail-stock-chip"><span class="stock-dot"></span>${t('In stock')}</span>`
        : `<span class="detail-stock-chip sold-out"><span class="stock-dot sold-out"></span>${t('Sold out')}</span>`;

      const deliveryEta = item.metadata?.delivery_eta;
      const deliveryText = deliveryEta ? `⚡ ${t('Delivery')} · ${esc(deliveryEta)}` : `⚡ ${t('Instant Delivery')}`;

      const heroMedia = safeImage
        ? `<div class="detail-hero-visual">
             <img src="${esc(safeImage)}" alt="" referrerpolicy="no-referrer">
             <span class="detail-overlay-chip">${deliveryText}</span>
           </div>`
        : `<div class="detail-hero-visual detail-hero-fallback">
             <div class="detail-hero-icon">${icon}</div>
             <span class="detail-hero-initial">${initial}</span>
             <span class="detail-overlay-chip">${deliveryText}</span>
           </div>`;

      const priceDisplay = money(selectedVariant.price, selectedVariant.currency);
      const totalPrice = money(Number(selectedVariant.price) * currentQty, selectedVariant.currency);

      const warrantyHtml = item.metadata?.warranty_days
        ? `<div class="detail-warranty-box">
             <span class="detail-warranty-icon">🛡️</span>
             <div>
               <strong>${t('Warranty')} · ${esc(item.metadata.warranty_days)} ${t('days')}</strong>
               <p>${esc(item.metadata.warranty_terms || t('Guaranteed delivery & full replacement via support.'))}</p>
             </div>
           </div>`
        : `<div class="detail-feature-strip">
             <span>🛡️ ${t('100% Guaranteed')}</span>
             <span>⚡ ${t('Instant Delivery')}</span>
             <span>💳 ${t('Wallet Checkout')}</span>
           </div>`;

      let optionsHtml = '';
      if (variants.length > 1) {
        optionsHtml = `
          <div class="detail-section">
            <h4 class="detail-section-heading">${t('Select Package')}</h4>
            <div class="detail-options-list">
              ${variants.map(v => {
                const isSel = v.id === selectedVariant.id;
                const vInStock = Number(v.stock_quantity) > 0;
                return `
                  <div class="detail-option-card ${isSel ? 'selected' : ''} ${vInStock ? '' : 'disabled'}" data-select-variant="${esc(v.id)}">
                    <div class="detail-option-left">
                      <div class="detail-radio-dot"></div>
                      <div class="detail-option-meta">
                        <span class="detail-option-title">${esc(v.title)}</span>
                        <span class="detail-option-sub">${vInStock ? format('available_count', {count: v.stock_quantity}) : t('Sold out')}</span>
                      </div>
                    </div>
                    <strong class="detail-option-price">${esc(money(v.price, v.currency))}</strong>
                  </div>
                `;
              }).join('')}
            </div>
          </div>
        `;
      } else if (variants.length === 1) {
        optionsHtml = `
          <div class="detail-single-option-box">
            <div>
              <strong>${esc(selectedVariant.title)}</strong>
              <small>${inStock ? t('In stock') : t('Sold out')}</small>
            </div>
            <strong class="detail-single-price">${priceDisplay}</strong>
          </div>
        `;
      }

      const guide = resolveVoucherGuide(item.title, categoryName, item.metadata || {});
      const isAr = (document.documentElement.lang === "ar");
      let voucherManualHtml = "";
      if (guide) {
        const steps = isAr ? (guide.steps_ar || guide.steps_en || []) : (guide.steps_en || guide.steps_ar || []);
        if (steps.length > 0) {
          voucherManualHtml = `
            <div class="detail-voucher-manual">
              <div class="detail-voucher-header">
                <div class="detail-voucher-title">
                  <span>📋</span>
                  <strong>${isAr ? 'طريقة الاستخدام وتفعيل الكود' : 'How to Redeem & Activate'}</strong>
                </div>
                ${guide.url ? `<a href="${esc(guide.url)}" target="_blank" rel="noopener noreferrer" class="detail-voucher-link">🌐 ${isAr ? 'الموقع الرسمي' : 'Official Site'} ↗</a>` : ''}
              </div>
              <ol class="detail-voucher-steps">
                ${steps.map(step => `<li>${esc(step)}</li>`).join('')}
              </ol>
            </div>
          `;
        }
      }

      el('productDetailContent').innerHTML = `
        <div class="detail-scroll-area">
          ${heroMedia}
          <div class="detail-header-block">
            <div class="detail-price-status-row">
              <span class="detail-active-price">${priceDisplay}</span>
              ${stockBadge}
            </div>
          </div>
          ${warrantyHtml}
          ${optionsHtml}
          ${voucherManualHtml}
          <div class="detail-section">
            <h4 class="detail-section-heading">${t('Description')}</h4>
            <p class="detail-description-text">${esc(item.description || t('Ready for instant checkout.'))}</p>
          </div>
        </div>

        <div class="detail-sticky-bar">
          <div class="detail-qty-control">
            <button type="button" class="detail-qty-btn" id="detailQtyMinus" ${currentQty <= 1 ? 'disabled' : ''} aria-label="Decrease">−</button>
            <span class="detail-qty-display" id="detailQtyDisplay">${currentQty}</span>
            <button type="button" class="detail-qty-btn" id="detailQtyPlus" ${currentQty >= Math.min(100, Number(selectedVariant.stock_quantity)) ? 'disabled' : ''} aria-label="Increase">+</button>
          </div>
          <button type="button" class="primary-button detail-add-cart-btn" id="detailAddToCartBtn" ${inStock ? '' : 'disabled'}>
            <span>${inStock ? t('Add to cart') : t('Sold out')}</span>
            ${inStock ? `<span class="detail-btn-price">· ${totalPrice}</span>` : ''}
          </button>
        </div>
      `;

      el('detailQtyMinus')?.addEventListener('click', () => {
        if (currentQty > 1) {
          currentQty--;
          renderDetailContent();
        }
      });
      el('detailQtyPlus')?.addEventListener('click', () => {
        const maxQ = Math.min(100, Number(selectedVariant.stock_quantity));
        if (currentQty < maxQ) {
          currentQty++;
          renderDetailContent();
        }
      });
      el('detailAddToCartBtn')?.addEventListener('click', () => {
        if (!inStock) return;
        if (typeof addToCart === 'function') {
          addToCart(selectedVariant.id, currentQty);
        }
        el('productDetailDialog').close();
      });

      el('productDetailContent').querySelectorAll('[data-select-variant]').forEach(card => {
        card.addEventListener('click', () => {
          const vId = card.dataset.selectVariant;
          const target = variants.find(v => v.id === vId);
          if (target && Number(target.stock_quantity) > 0) {
            selectedVariant = target;
            currentQty = 1;
            renderDetailContent();
          }
        });
      });
    }

    renderDetailContent();
    el('productDetailDialog').showModal();
    window.Telegram?.WebApp?.BackButton?.show?.();
  }
  async function history(append=false){
    if(historyBusy&&append)return;
    if(append&&historyOffset===null)return;
    const rev=++historyRevision,kind=el('accountHistoryKind').value;historyBusy=true;el('walletHistoryMore').disabled=true;
    const endpoint={spending:'/wallet-history',assets:'/asset-wallet-history',funding:'/wallet-funding-history?kind=standard',deposits:'/wallet-funding-history?kind=flexible'}[kind];
    try{const page=await api(`/api/v1/storefront${endpoint}${endpoint.includes('?')?'&':'?'}offset=${append?historyOffset:0}`);if(rev!==historyRevision)return;
      const amount=row=>row.currency?money(row.amount,row.currency):`${row.amount??'—'} ${row.asset||''}${row.network?' · '+row.network:''}`;
      const html=page.items.map(row=>`<article class="history-row"><div><strong>${esc(t(row.type||row.status))}</strong><small>${esc(new Date(row.created_at).toLocaleString(document.documentElement.lang))}</small>${row.kind?`<button type="button" class="text-button" data-payment-history="${esc(row.id)}" data-payment-kind="${esc(row.kind)}">${t('View payment')}</button>`:''}</div><div><strong dir="auto">${esc(amount(row))}</strong>${row.balance_after!=null?`<small>${t('Balance after')}: ${esc(row.currency?money(row.balance_after,row.currency):row.balance_after)}</small>`:''}</div></article>`).join('');
      if(append)el('walletHistory').insertAdjacentHTML('beforeend',html);else el('walletHistory').innerHTML=html||`<p class="field-help">${t('No activity yet.')}</p>`;
      historyOffset=page.next_offset;el('walletHistoryMore').classList.toggle('hidden',historyOffset===null);
    }catch(error){if(rev===historyRevision)toast(error.message,'error');}finally{if(rev===historyRevision){historyBusy=false;el('walletHistoryMore').disabled=false;}}
  }
  document.addEventListener('click',event=>{
    const detail=event.target.closest('[data-product-detail]');if(detail)product(detail.dataset.productDetail);
    const payment=event.target.closest('[data-payment-history]');if(payment)openPayment(payment.dataset.paymentHistory,payment.dataset.paymentKind).catch(error=>toast(error.message,'error'));
  });
  el('productDetailDialog').addEventListener('close',()=>{if(!document.querySelector('.cart-sheet.open'))window.Telegram?.WebApp?.BackButton?.hide?.();});
  el('closeProductDetail').onclick=()=>el('productDetailDialog').close();
  el('productDetailDialog').onclick=event=>{if(event.target!==el('productDetailDialog'))return;const r=event.target.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)event.target.close();};
  el('accountHistoryKind').onchange=()=>history();
  el('walletHistoryButton').onclick=()=>history();
  el('walletHistoryMore').onclick=()=>history(true);
  return {renderHelp,history};
}

export function bindSheetAccess(sheet,onClose){
  sheet.setAttribute('role','dialog');sheet.setAttribute('aria-modal','true');sheet.inert=true;
  let prior=null,active=false;
  const focusable=()=>[...sheet.querySelectorAll('button,input,select,textarea,a[href]')].filter(node=>!node.disabled&&node.getClientRects().length);
  function sync(){
    const open=sheet.classList.contains('open');sheet.inert=!open;
    if(open&&!active){prior=document.activeElement;active=true;focusable()[0]?.focus();}
    const restoreFocus=!open&&active;
    if(restoreFocus)active=false;
    const anyOpen=Boolean(document.querySelector('.cart-sheet.open'));
    document.getElementById('app').inert=anyOpen;
    document.getElementById('bottomNav').inert=anyOpen;
    document.getElementById('openCartButton').inert=anyOpen;
    document.body.style.overflow=anyOpen?'hidden':'';
    if(restoreFocus&&!anyOpen&&prior?.isConnected)prior.focus();
  }
  new MutationObserver(sync).observe(sheet,{attributes:true,attributeFilter:['class']});
  sheet.addEventListener('keydown',event=>{
    if(event.key==='Escape'){event.preventDefault();onClose();}
    if(event.key==='Tab'){const list=focusable();if(!list.length){event.preventDefault();return;}const index=list.indexOf(document.activeElement);if(event.shiftKey&&index<=0){event.preventDefault();list.at(-1).focus();}else if(!event.shiftKey&&(index===list.length-1||index<0)){event.preventDefault();list[0].focus();}}
  });
}
