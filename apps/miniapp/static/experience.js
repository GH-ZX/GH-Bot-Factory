/* Read-only customer details. All purchasing still goes through the existing cart API. */
export function bindExperience({state,api,esc,money,t,toast,openPayment}){
  const el=id=>document.getElementById(id);
  let historyOffset=null,historyBusy=false,historyRevision=0;
  function renderHelp(){
    const settings=state.bootstrap?.store?.settings||{};
    el('salesPauseBanner').textContent=t('New purchases are temporarily paused. Existing orders and support remain available.');
    el('salesPauseBanner').classList.toggle('hidden',settings.sales_paused!==true);
    el('storeNotice').textContent=settings.store_notice||'';
    el('storeNotice').classList.toggle('hidden',!settings.store_notice);
    el('helpStoreDescription').textContent=settings.store_description||'';
    el('storeFAQ').innerHTML=(Array.isArray(settings.faq)?settings.faq:[]).slice(0,12).map(item=>`<details class="faq-item"><summary>${esc(item.question)}</summary><p>${esc(item.answer)}</p></details>`).join('');
    const safe=value=>{try{const u=new URL(value);return u.protocol==='https:'&&!u.username&&!u.password?u.href:null;}catch(_){return null;}};
    el('storePolicyLinks').innerHTML=[['support_url','Contact the store'],['terms_url','Terms'],['privacy_url','Privacy']].map(([key,label])=>{const url=safe(settings[key]);return url?`<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${t(label)} ↗</a>`:'';}).join('');
    document.querySelector('label[for="recipientInput"]').textContent=settings.recipient_label||t('Delivery recipient');
    el('recipientHelp').textContent=settings.recipient_help||t('Enter the destination required by the product provider.');
  }
  function product(id){
    const item=state.catalog.products.find(p=>p.id===id);if(!item)return;
    const image=item.metadata?.image_url||item.metadata?.thumbnail_url;let safeImage='';
    try{const url=new URL(image,location.origin);if(image&&url.protocol==='https:'&&!url.username&&!url.password)safeImage=url.href;}catch(_){}
    el('productDetailTitle').textContent=item.title;
    el('productDetailContent').innerHTML=`${safeImage?`<img class="detail-image" src="${esc(safeImage)}" alt="" referrerpolicy="no-referrer">`:''}<p class="detail-description">${esc(item.description||t('Ready for instant checkout.'))}</p>${item.metadata?.warranty_days?`<div class="detail-warranty"><strong>${t('Warranty')} · ${esc(item.metadata.warranty_days)} ${t('days')}</strong><p>${esc(item.metadata.warranty_terms||'')}</p></div>`:''}<h3>${t('Choose an option')}</h3>${item.variants.map(v=>`<article class="detail-option"><div><strong>${esc(v.title)}</strong><small>${esc(v.sku)}</small><span>${esc(money(v.price,v.currency))}</span></div><button type="button" class="primary-button" data-add-variant="${esc(v.id)}" ${Number(v.stock_quantity)>0?'':'disabled'}>${t(Number(v.stock_quantity)>0?'Add to cart':'Sold out')}</button></article>`).join('')}<p class="field-help">${t('Payment uses your wallet. Delivery details appear in your orders.')}</p>`;
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
