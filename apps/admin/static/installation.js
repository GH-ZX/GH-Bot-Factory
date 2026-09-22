/* Owner-reported evidence never substitutes for observed acceptance. */
const Installation = (() => {
  async function open(){
    let dialog=document.getElementById('installationDialog');
    if(!dialog){dialog=document.createElement('dialog');dialog.id='installationDialog';dialog.setAttribute('aria-label','Installation and backup evidence');document.body.append(dialog);}
    dialog.innerHTML='<p role="status">Loading installation evidence…</p>';dialog.showModal();
    try{
      const data=await api('/api/v1/admin/installation'),p=data.reported,esc=escapeHtml;
      const field=(key,label,value='')=>`<label>${label}<input name="${key}" value="${esc(value||'')}" maxlength="255"></label>`;
      dialog.innerHTML=`<form id="installationEvidenceForm"><div class="dialog-head"><h2>Installation & backups</h2><button type="button" data-install-close aria-label="Close">×</button></div><p class="muted">These records are supplied by the owner. They do not execute backups or prove that restoration works. Keep secrets out of references.</p><p>Observed database schema: <code>${esc(data.observed_schema_revisions.join(', '))}</code></p>${field('image','Installed immutable image',p.image)}${field('release_version','Installed release version',p.release_version)}<label>Last backup outcome<select name="backup_status">${['NOT_RECORDED','COMPLETED','FAILED'].map(v=>`<option ${p.backup_status===v?'selected':''}>${v}</option>`).join('')}</select></label>${field('backup_reference','Private backup reference (no passwords)',p.backup_reference)}${field('database_backup_sha256','Database backup SHA-256',p.database_backup_sha256)}${field('vault_backup_sha256','Vault backup SHA-256',p.vault_backup_sha256)}${field('backup_completed_at','Actual backup time with timezone (ISO 8601)',p.backup_completed_at)}${field('restore_reference','Restore drill evidence reference, if performed',p.restore_reference)}<p id="installationEvidenceError" role="alert"></p><div class="dialog-actions"><button type="button" class="ghost" data-install-report>Download safe support report</button><button type="submit" class="primary">Save owner-reported evidence</button></div></form>`;
      const form=dialog.querySelector('form');
      const owner=state.bootstrap?.actor?.role==='OWNER';
      form.querySelectorAll('input,select,[type=submit],[data-install-report]').forEach(n=>n.disabled=!owner);
      dialog.querySelector('[data-install-close]').onclick=()=>dialog.close();
      form.onsubmit=async event=>{event.preventDefault();const button=form.querySelector('[type=submit]');button.disabled=true;
        try{const values=Object.fromEntries(new FormData(form));values.backup_completed_at=values.backup_completed_at||null;const result=await api('/api/v1/admin/installation',{method:'PUT',body:JSON.stringify({...values,expected_version:p.version})});p.version=result.reported.version;el('installationEvidenceError').textContent='Evidence saved. Acceptance remains separate.';}catch(error){el('installationEvidenceError').textContent=error.message;}finally{button.disabled=false;}};
      dialog.querySelector('[data-install-report]').onclick=async()=>{try{const report=await api('/api/v1/admin/installation/support-report');const url=URL.createObjectURL(new Blob([JSON.stringify(report,null,2)],{type:'application/json'}));const link=document.createElement('a');link.href=url;link.download='support-diagnostics.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(error){el('installationEvidenceError').textContent=error.message;}};
    }catch(error){dialog.replaceChildren();const p=document.createElement('p');p.setAttribute('role','alert');p.textContent=error.message;const button=document.createElement('button');button.textContent='Close';button.onclick=()=>dialog.close();dialog.append(p,button);}
  }
  document.addEventListener('click',event=>{if(event.target.closest('[data-installation-open]'))open().catch(showHomeError);});
  return {open};
})();
