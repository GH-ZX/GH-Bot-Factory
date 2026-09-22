/* Observations are separate from authoritative resolution actions. */
const Attention = (() => {
  let revision=0;
  const root=()=>el('attentionContent');
  async function load(){
    const current=++revision;
    root().innerHTML='<p role="status">Loading store observations…</p>';
    try {
      const data=await api('/api/v1/admin/attention');if(current!==revision)return;
      const total=Object.values(data.totals).reduce((sum,n)=>sum+n,0);
      root().innerHTML=`<article class="panel"><small class="eyebrow">STORE OPERATIONS</small><h2>${total?`${total} observations need attention`:'No current alerts in the monitored categories'}</h2><p class="muted">Configuration and recorded events only. A clear panel does not establish successful payments, backups or production readiness.</p><button type="button" class="ghost" data-attention-refresh>Refresh observations</button></article><div class="settings-layout"><div>${data.alerts.map(row=>`<article class="panel"><span class="chip">${row.acknowledged?'Acknowledged · still open':'Needs review'}</span><h3>${escapeHtml(row.title)}</h3><p>${escapeHtml(row.guidance)}</p><p class="muted">Record <code>${escapeHtml(row.record_id)}</code></p><div class="settings-save"><button class="primary" type="button" data-attention-view="${escapeHtml(row.view)}">Open resolution workspace</button>${!row.acknowledged&&['OWNER','ADMIN'].includes(state.bootstrap?.actor?.role)?`<button class="ghost" type="button" data-attention-key="${escapeHtml(row.key)}" data-fingerprint="${escapeHtml(row.fingerprint)}">Acknowledge for 24 hours</button>`:''}</div></article>`).join('')}${data.truncated?'<p class="muted">Showing up to 30 records per business category and observations for 100 bots. Open the relevant workspace for the full list.</p>':''}</div><aside><article class="panel"><h3>Configuration checklist</h3><p class="muted">Review missing items before preparing a customer handoff.</p>${data.checks.map(c=>`<button class="settings-link" type="button" data-attention-view="${escapeHtml(c.view)}"><span>${c.configured?'✓':'○'} ${escapeHtml(c.label)}</span><span>${c.configured?'Configured':'Review'}</span></button>`).join('')}</article><article class="panel"><h3>What acknowledgement means</h3><p>It records that you saw this observation. It does not resolve an order, credit money or silence a changed observation. Resolved conditions disappear when refreshed.</p></article></aside></div><p role="alert" id="attentionError"></p>`;
    }catch(error){if(current===revision)root().innerHTML=`<article class="panel"><p role="alert">${escapeHtml(error.message)}</p><button class="ghost" data-attention-refresh>Try again</button></article>`;}
  }
  root().addEventListener('click',async event=>{
    const button=event.target.closest('button');if(!button)return;
    try{
      if(button.hasAttribute('data-attention-refresh'))return await load();
      if(button.dataset.attentionView)return await navigateTo(button.dataset.attentionView);
      if(button.dataset.attentionKey){button.disabled=true;await api('/api/v1/admin/attention/acknowledge',{method:'POST',body:JSON.stringify({key:button.dataset.attentionKey,fingerprint:button.dataset.fingerprint})});await load();}
    }catch(error){const target=el('attentionError');if(target)target.textContent=error.message;button.disabled=false;}
  });
  return {load};
})();
