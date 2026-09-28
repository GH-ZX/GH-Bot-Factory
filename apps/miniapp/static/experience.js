export function bindExperience({state,api,esc,money,t,format,toast,openPayment,addToCart,openCart}){
  const el=id=>document.getElementById(id);
  let historyOffset=null,historyBusy=false,historyRevision=0;

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
