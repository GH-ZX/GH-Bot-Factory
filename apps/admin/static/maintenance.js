/* Customer-owned installation care. No actions execute commands on a server. */
const Maintenance = (() => {
  const root='/api/v1/admin/maintenance';
  let data=null, busy=false, generation=0;
  const esc=escapeHtml, box=()=>el('maintenanceContent');
  const owner=()=>state.bootstrap?.actor?.role==='OWNER';
  const send=(path,body,method='POST')=>api(root+path,{method,body:JSON.stringify(body)});
  const button=(action,label,id='')=>`<button type="button" class="ghost" data-care="${action}" data-id="${esc(id)}">${label}</button>`;
  const field=(name,label,extra='')=>`<label>${label}<input name="${name}" required ${extra}></label>`;
  const area=(name,label)=>`<label>${label}<textarea name="${name}" required rows="4" minlength="10" maxlength="4000"></textarea></label>`;
  function form(title,html,action){
    box().innerHTML=`<h3>${esc(title)}</h3><form id="maintenanceForm">${html}<p class="muted">Keep passwords, customer details and tokens out of notes.</p><p id="maintenanceError" class="signin-error" role="alert"></p><div class="dialog-actions">${button('back','Back')}<button class="primary" type="submit">Save</button></div></form>`;
    el('maintenanceForm').onsubmit=async event=>{
      event.preventDefault();if(busy)return;busy=true;const submit=event.target.querySelector('[type=submit]');submit.disabled=true;
      try{await action(Object.fromEntries(new FormData(event.target)));}catch(e){if(el('maintenanceError'))el('maintenanceError').textContent=e.message;}finally{busy=false;submit.disabled=false;}
    };
  }
  async function load(){
    const rev=++generation;const result=await api(root);if(rev!==generation)return;data=result;
    const manages=owner();
    box().innerHTML=`<p class="muted">Track bot problems, fixes, and approved updates. This installation remains independent of the factory.</p><div class="ops-actions">${manages?button('new','Report a bot problem'):''}${button('back','Refresh')}</div><h3>Reported problems</h3>${data.issues.length?data.issues.map(i=>`<article class="brief-card"><h4>${esc(i.title)}</h4><p>${esc(i.status)} · ${esc(i.scope)}${i.fix_version?' · '+esc(i.fix_version):''}</p>${button('issue','History & next step',i.id)}${manages?button('grant','Allow diagnostics',i.id)+button('update','Plan an update',i.id):''}</article>`).join(''):'<p class="muted">No problems reported yet.</p>'}<h3>Temporary support access</h3><p class="muted">Only schema version and bot counts are shared. No secrets, raw logs, customer profiles or orders. Access is read-only.</p>${data.grants.map(g=>`<article class="brief-card"><p>Expires ${esc(new Date(g.expires_at).toLocaleString())} · ${g.revoked_at?'Revoked':new Date(g.expires_at)>new Date()?'Active':'Expired'}</p>${manages&&!g.revoked_at?button('revoke','Revoke access',g.id):''}</article>`).join('')||'<p class="muted">No access granted.</p>'}<h3>Release & update history</h3><p class="muted">Statuses record owner-reported actions. They do not deploy or verify a server.</p>${data.updates.map(u=>`<article class="brief-card"><h4>${esc(u.version_label)} · ${esc(u.status)}</h4><p>${esc(u.release_notes)}</p><p>Migration: ${esc(u.migration_notes)}</p><p class="maintenance-reference">${esc(u.image)}</p>${u.backup_reference?`<p>Backup: ${esc(u.backup_reference)}</p>`:''}${manages?button('progress','Record next step',u.id):''}</article>`).join('')||'<p class="muted">No updates planned.</p>'}`;
  }
  async function history(id){
    const issue=await api(`${root}/issues/${id}`);
    const records=`<h4>History · latest 200 events</h4>${issue.events.map(e=>`<article class="brief-card"><strong>${esc(e.kind)}</strong><small> · ${esc(new Date(e.created_at).toLocaleString())}</small><p class="maintenance-body">${esc(e.body)}</p></article>`).join('')}`;
    if(!owner()){box().innerHTML=`<h3>${esc(issue.title)}</h3>${records}${button('back','Back')}`;return;}
    form(issue.title,`<p>${esc(issue.description)}</p><p class="muted">Affected release: ${esc(issue.affected_version||"Not recorded")}${issue.source_case_id?` · Support case ${esc(issue.source_case_id)}`:""}</p><label>Status<select name="status">${['OPEN','DIAGNOSING','FIX_AVAILABLE','RESOLVED'].map(s=>`<option ${s===issue.status?'selected':''}>${s}</option>`).join('')}</select></label><label>Fix scope<select name="scope">${['UNDECIDED','SHARED_CORE','TENANT_CUSTOM'].map(s=>`<option ${s===issue.scope?'selected':''}>${s}</option>`).join('')}</select></label><label>Fix version<input name="fix_version" maxlength="80" value="${esc(issue.fix_version||'')}"></label>${area('body','Diagnosis, fix or resolution notes')}${records}`,async values=>{await send(`/issues/${id}`,{...values,fix_version:values.fix_version||null,expected_version:issue.version},'PATCH');await load();});
  }
  box().onclick=async event=>{
    const b=event.target.closest('[data-care]');if(!b||busy)return;
    const id=b.dataset.id;
    try{
      switch(b.dataset.care){
      case 'back':await load();break;
      case 'new':form('Report a bot problem',field('title','Short title','minlength="3" maxlength="160"')+`<label>Affected version (optional)<input name="affected_version" maxlength="80"></label><label>Related customer support case ID (optional)<input name="source_case_id" placeholder="Case UUID"></label>`+area('description','Sanitized reproduction — do not copy customer secrets'),async v=>{await send('/issues',{...v,affected_version:v.affected_version||null,source_case_id:v.source_case_id||null});await load();});break;
      case 'issue':await history(id);break;
      case 'grant':form('Authorize temporary diagnostics',`<p>This allows anyone holding the code to read the limited diagnostics described above. Share it privately with your support contact.</p>${field('hours','Expires after (hours)','type="number" min="1" max="72" value="24"')}`,async v=>{
        const rev=generation;const grant=await send('/grants',{issue_id:id,hours:Number(v.hours)});
        if(rev!==generation||!el('maintenanceDialog').open)return;
        box().innerHTML=`<h3>Support access created</h3><p>Copy this code now. It is shown once and is not saved in your browser.</p><textarea id="supportGrantValue" readonly rows="3"></textarea><p>Send this installation URL and code privately to your support contact. They use the code in the X-Support-Token header at /api/v1/maintenance-access/diagnostics.</p><p>Expires ${esc(new Date(grant.expires_at).toLocaleString())}.</p>${button('back','Done')}`;el('supportGrantValue').value=grant.access_token;
      });break;
      case 'revoke':await send(`/grants/${id}/revoke`,{});await load();break;
      case 'update':form('Plan a customer-approved update',field('version_label','Release version','maxlength="80"')+field('image','New immutable image (registry/path@sha256:…)','maxlength="255"')+field('previous_image','Current immutable image','maxlength="255"')+area('release_notes','What changes')+area('migration_notes','Database migration and restore requirements'),async v=>{await send('/updates',{issue_id:id,...v});await load();});break;
      case 'progress':{
        const item=data.updates.find(u=>u.id===id), next={PROPOSED:['APPROVED','CANCELLED'],APPROVED:['BACKED_UP','CANCELLED'],BACKED_UP:['INSTALLED','ROLLED_BACK','CANCELLED'],INSTALLED:['ROLLED_BACK']}[item.status]||[];
        if(!next.length){box().innerHTML=`<p>This update is closed.</p>${button('back','Back')}`;break;}
        form('Record an update step',`<p>Approval must come from the customer installation owner. Before recording installation, stop traffic, back up the database and encrypted vault, then follow the package update guide. Rollback may require restoring both database and vault.</p><label>Step<select name="status">${next.map(s=>`<option>${s}</option>`).join('')}</select></label><label>Database and vault backup reference<input name="backup_reference" maxlength="255" value="${esc(item.backup_reference||'')}"></label><label>Actual installed image (required for installed/rolled back)<input name="installed_image" maxlength="255"></label><label>Actual schema revision (required for installed/rolled back)<input name="schema_revision" maxlength="64"></label>${area('evidence','Approval, backup or deployment evidence')}`,async v=>{await send(`/updates/${id}`,{...v,backup_reference:v.backup_reference||null,installed_image:v.installed_image||null,schema_revision:v.schema_revision||null,expected_version:item.version},'PATCH');await load();});break;
      }}
    }catch(e){box().insertAdjacentHTML('afterbegin',`<p class="signin-error" role="alert">${esc(e.message)}</p>`);}
  };
  el('openMaintenance').onclick=()=>{el('maintenanceDialog').showModal();load().catch(e=>{box().textContent=e.message;});};
  el('closeMaintenance').onclick=()=>el('maintenanceDialog').close();
  el('maintenanceDialog').addEventListener('close',()=>{++generation;box().replaceChildren();data=null;});
  function reportFromCase(id){
    el('operationsDialog').close();el('maintenanceDialog').showModal();
    form('Document a bot issue',field('title','Short title','minlength="3" maxlength="160"')+`<label>Affected version<input name="affected_version" maxlength="80"></label>`+area('description','Sanitized reproduction — no customer secrets'),async values=>{await send('/issues',{...values,affected_version:values.affected_version||null,source_case_id:id});await load();});
  }
  return {load,reportFromCase};
})();
