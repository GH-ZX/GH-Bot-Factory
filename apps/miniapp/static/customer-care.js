/* Shopper support uses the same durable case history as the tenant's inbox. */
export function bindCustomerCare({api, escapeHtml:esc, toast, orders, t}) {
  const root=document.getElementById('supportPanel');
  let current=null, busy=false, revision=0;
  const request=async(path='',options={})=>api('/api/v1/storefront/support'+path,options);
  async function list(){
    const rev=++revision;
    const rows=await request(); if(rev!==revision)return;
    current=null;
    root.innerHTML=`<div class="section-heading"><h2>${t('Support')}</h2><button class="text-button" data-new-case>${t('New ticket')}</button></div>${rows.length?rows.map(row=>`<button class="support-case" data-case="${esc(row.id)}"><strong>${esc(row.subject)}</strong><span>${esc(t(row.status))}</span><small>${esc(new Date(row.created_at).toLocaleDateString())}</small></button>`).join(''):`<p class="field-help">${t('Your conversations with the store appear here.')}</p>`}`;
  }
  function compose(orderId='',itemId=''){
    ++revision;current=null;
    root.innerHTML=`<div class="section-heading"><h2>${t(itemId?'Warranty request':'New ticket')}</h2><button class="text-button" data-support-back>${t('Back')}</button></div><form id="supportCompose"><label class="field-label">${t('Subject')}<input class="recipient-input" name="subject" minlength="3" maxlength="160" required></label><label class="field-label">${t('Order (optional)')}<select name="order_id" class="recipient-input"><option value="">${t('General question')}</option>${orders().map(o=>`<option value="${esc(o.id)}" ${o.id===orderId?'selected':''}>${esc(o.order_number)}</option>`).join('')}</select></label><label class="field-label">${t('Message')}<textarea name="body" class="recipient-input" rows="4" maxlength="3000" required></textarea></label><p class="field-help">${t('Describe what happened. Never send passwords or bot tokens.')}</p><button class="primary-button">${t('Send ticket')}</button><p class="support-error" role="alert"></p></form>`;
    const form=root.querySelector('form');
    if(itemId)form.elements.order_id.disabled=true;
    form.onsubmit=event=>perform(event,async()=>{
      const payload={subject:form.elements.subject.value.trim(),body:form.elements.body.value.trim(),order_id:form.elements.order_id.value||null,warranty_item_id:itemId||null};
      const row=await request('',{method:'POST',body:JSON.stringify(payload)});show(row);
    });
    root.querySelector('input').focus();
  }
  function show(row){
    current=row;
    root.innerHTML=`<div class="section-heading"><h2>${esc(row.subject)}</h2><button class="text-button" data-support-back>${t('Back')}</button></div><p class="field-help">${esc(t(row.status))}</p><div class="support-messages">${row.messages.map(m=>`<article class="support-message"><small>${esc(new Date(m.created_at).toLocaleString())}</small><p>${esc(m.body)}</p></article>`).join('')}</div><form id="supportReply"><label class="field-label">${t('Reply')}<textarea name="body" class="recipient-input" rows="3" maxlength="3000" required></textarea></label><button class="primary-button">${t('Send reply')}</button><p class="support-error" role="alert"></p></form>`;
    root.querySelector('form').onsubmit=event=>perform(event,async()=>{
      const body=event.target.elements.body.value.trim();
      const row=await request(`/${current.id}/reply`,{method:'POST',body:JSON.stringify({body,expected_version:current.version})});show(row);
    });
  }
  async function perform(event,action){
    event.preventDefault();if(busy)return;busy=true;
    const form=event.target, button=form.querySelector('button');button.disabled=true;
    try{await action();}catch(e){form.querySelector('.support-error').textContent=e.message;}finally{busy=false;button.disabled=false;}
  }
  root.onclick=async event=>{
    if(busy)return;
    try{
      if(event.target.closest('[data-new-case]'))compose();
      if(event.target.closest('[data-support-back]'))await list();
      const button=event.target.closest('[data-case]');if(button){const rev=++revision;const row=await request('/'+button.dataset.case);if(rev===revision)show(row);}
    }catch(e){toast(e.message,'error');}
  };
  document.addEventListener('click',event=>{
    const button=event.target.closest('[data-order-support]');if(!button||busy)return;
    const navBtn=document.querySelector('[data-target="settings"], [data-target="account"], [data-target="help"]');
    if(navBtn)navBtn.click();
    compose(button.dataset.orderSupport,button.dataset.warrantyItem||'');root.scrollIntoView({behavior:'smooth'});
  });
  document.getElementById('supportButton').onclick=()=>list().catch(e=>toast(e.message,'error'));
  return {list,compose};
}
