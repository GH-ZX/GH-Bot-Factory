/* Shared presentation helpers. Template keys and prices remain server-owned. */
window.StoreSetup = (() => {
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const groups = [
    {id:'general', name:'General store', description:'A mixed catalog or a business you are still shaping.', keys:['general-commerce','hybrid-store','reseller-hub'], sample:'Sample product'},
    {id:'digital', name:'Digital products', description:'Software, gaming products, subscriptions, and accounts.', keys:['digital-goods','digital-reseller','gaming-store','accounts-store'], sample:'Sample digital download'},
    {id:'gifts', name:'Gift cards & codes', description:'Prepaid cards, vouchers, and redeemable codes.', keys:['gift-cards','gift-reseller'], sample:'Sample gift card'},
    {id:'services', name:'Services', description:'Design, consulting, setup, and other assisted work.', keys:['services'], sample:'Sample service package'},
    {id:'numbers', name:'Numbers & SMS', description:'Phone number reservations from a connected supplier.', keys:['numbers-sms'], sample:'Sample number reservation'},
  ];
  const sources = {stored:'My own stock or services', provider_api:'A connected supplier', hybrid:'Both stock and suppliers'};
  const groupFor = key => groups.find(g => g.keys.includes(key)) || groups[0];
  function recommend(group, source, templates) {
    const keys = {
      general:{stored:'general-commerce',provider_api:'reseller-hub',hybrid:'hybrid-store'},
      digital:{stored:'digital-goods',provider_api:'digital-reseller',hybrid:'digital-reseller'},
      gifts:{stored:'gift-cards',provider_api:'gift-reseller',hybrid:'gift-reseller'},
      services:{stored:'services',provider_api:'services',hybrid:'services'},
      numbers:{stored:'numbers-sms',provider_api:'numbers-sms',hybrid:'numbers-sms'},
    };
    return templates.find(t => t.key === keys[group]?.[source]) || templates.find(t => groupFor(t.key).id === group);
  }
  function chooser(root, {templates, key, source, onChange}) {
    let selected = key, selectedSource = source || templates.find(t=>t.key===key)?.guidance?.product_source || 'stored';
    const draw = () => {
      const group = groupFor(selected);
      if(group.id==="numbers")selectedSource="provider_api";
      if(group.id==="services")selectedSource="stored";
      root.innerHTML = `<div class="setup-categories" role="group" aria-label="What do you sell?">${groups.filter(g=>templates.some(t=>g.keys.includes(t.key))).map(g=>`<button type="button" class="setup-category" data-group="${g.id}" aria-pressed="${g.id===group.id}"><strong>${g.name}</strong><span>${g.description}</span></button>`).join('')}</div>
        <label class="setup-source">Where will products come from?<select data-source>${Object.entries(sources).map(([v,n])=>`<option value="${v}" ${(group.id==="numbers"&&v!=="provider_api")||(group.id==="services"&&v!=="stored")?"disabled":""} ${v===selectedSource?'selected':''}>${n}</option>`).join('')}</select></label>
        <p class="setup-recommendation" aria-live="polite">${escape(templates.find(t=>t.key===selected)?.name || selected)} is selected for ${escape(group.name.toLowerCase())}. ${group.id==='numbers'?'You will need a number supplier before selling.':group.id==='services'?'Service delivery is managed by your team.':`Start with ${escape(sources[selectedSource].toLowerCase())}.`} Appearance is chosen separately.</p>
        <details class="setup-details"><summary>Choose a specialist preset instead</summary><label>All available presets<select data-preset>${templates.map(t=>`<option value="${escape(t.key)}" ${t.key===selected?'selected':''}>${escape(t.name)}</option>`).join('')}</select></label><p class="muted">Use this if you already know which setup you need. Changing presets does not change your store name or colors.</p></details>`;
    };
    root.onclick = event => {
      const button = event.target.closest('[data-group]');
      if (!button) return;
      const t = recommend(button.dataset.group, selectedSource, templates);
      if (!t) return;
      selected=t.key; draw(); root.querySelector(`[data-group="${button.dataset.group}"]`)?.focus(); onChange(selected,selectedSource);
    };
    root.onchange = event => {
      if (event.target.matches('[data-source]')) {
        selectedSource=event.target.value;
        selected=recommend(groupFor(selected).id,selectedSource,templates)?.key || selected;
      } else if(event.target.matches('[data-preset]')) selected=event.target.value;
      else return;
      const control=event.target.matches('[data-source]')?'[data-source]':'[data-preset]';
      draw(); if(control==='[data-preset]') root.querySelector('details').open=true;
      root.querySelector(control).focus(); onChange(selected,selectedSource);
    };
    draw();
  }
  function preview(root, options = {}) {
    let step=0, mode='store';
    const group=groupFor(options.key);
    const name=options.name || 'Your store';
    const paint=()=>{
      root.innerHTML=`<div class="setup-preview-head"><strong>${escape(name)}</strong><span class="setup-demo-label">Interactive demo</span></div><p class="muted">Simulated examples only. No payment, reservation, or order is created. Actual delivery depends on your setup.</p><div class="setup-preview-tabs" role="group" aria-label="Preview format"><button type="button" data-mode="store" aria-pressed="${mode==='store'}">Storefront</button><button type="button" data-mode="chat" aria-pressed="${mode==='chat'}">Telegram chat</button></div><div class="setup-demo-content ${mode==='chat'?'setup-chat':'setup-store'}" aria-live="polite">${mode==='chat'?'<small>Bot conversation · demo</small>':`<div class="setup-product-art" aria-hidden="true">${escape(group.name.charAt(0))}</div>`}<h3>${escape(step===0?(options.tagline||'Welcome to your store'):step===1?'Review sample order':'Example order update')}</h3><p>${escape(step===0?group.sample:step===1?`${group.sample} · Example price: 10.00 USD`:(group.id==='services'?'Request received. Your team would arrange delivery.':group.id==='numbers'?'A connected supplier would return the reservation status here.':'Your delivery details would appear here after successful fulfillment.'))}</p>${step===2?'<p class="muted">Preview complete. This does not confirm your store is ready to launch.</p>':''}</div><button type="button" class="setup-demo-next">${['Browse sample product','Simulate purchase','Start again'][step]}</button>`;
      root.style.setProperty('--preview-accent', /^#[0-9a-f]{6}$/i.test(options.accent||'')?options.accent:'#7c6cff');
    };
    root.onclick=event=>{
      const tab=event.target.closest('[data-mode]');
      if(tab){mode=tab.dataset.mode;paint();root.querySelector(`[data-mode="${mode}"]`).focus();}
      if(event.target.closest('.setup-demo-next')){step=(step+1)%3;paint();root.querySelector('.setup-demo-next').focus();}
    };
    paint();
  }
  function readDraft(key) {try {const d=JSON.parse(sessionStorage.getItem(key)); return d?.version===1 && Date.now()-d.savedAt<86400000 ? d : null;}catch(_){return null;}}
  function saveDraft(key, value) {try{sessionStorage.setItem(key,JSON.stringify({...value,version:1,savedAt:Date.now()}));return true;}catch(_){return false;}}
  function clearDraft(key){try{sessionStorage.removeItem(key);}catch(_){}}
  return {chooser,preview,groupFor,readDraft,saveDraft,clearDraft};
})();
