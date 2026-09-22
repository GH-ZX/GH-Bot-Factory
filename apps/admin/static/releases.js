/* Immutable tenant release notes link reported issues to installable images. */
const ReleaseCatalog=(()=>{
  const esc=escapeHtml,base='/api/v1/admin/maintenance';
  let rows=[],issues=[],busy=false;
  const content=()=>el('releaseContent');
  const owner=()=>state.bootstrap?.actor?.role==='OWNER';
  const field=(name,label,value='')=>`<label>${label}<input name="${name}" value="${esc(value)}" required maxlength="255"></label>`;
  const area=(name,label)=>`<label>${label}<textarea name="${name}" rows="3" minlength="10" maxlength="4000" required></textarea></label>`;
  async function load(){
    const [catalog,care]=await Promise.all([api(base+'/releases/catalog'),api(base)]);rows=catalog;issues=care.issues;
    content().innerHTML=`<p class="muted">A release records exactly what changed, the fixed issues, and the image to install. Publishing this record does not deploy or verify the software.</p>${owner()?'<button type="button" class="primary" data-release-new>Record a release</button>':''}<div class="stack">${rows.map(row=>`<article class="panel"><h3>${esc(row.version_label)}</h3><p class="maintenance-body">${esc(row.release_notes)}</p><p class="maintenance-reference">${esc(row.image)}</p><details><summary>Database & rollback notes</summary><p class="maintenance-body">${esc(row.migration_notes)}</p><p class="maintenance-body">${esc(row.rollback_notes)}</p></details><h4>Linked fixes</h4>${row.issue_ids.map(id=>{const issue=issues.find(i=>i.id===id);return `<p>${esc(issue?.title||id)}${issue?` · ${esc(issue.scope)} · affected ${esc(issue.affected_version||'version not recorded')}`:''}</p>`;}).join('')||'<p class="muted">Feature release with no linked incident.</p>'}${owner()&&row.issue_ids.length?`<button type="button" class="ghost" data-release-update="${esc(row.id)}">Plan customer update</button>`:''}</article>`).join('')||'<p class="muted">No releases recorded yet.</p>'}</div>`;
  }
  function form(title,html,action){
    content().innerHTML=`<h3>${esc(title)}</h3><form id="releaseForm">${html}<p id="releaseError" class="signin-error" role="alert"></p><div class="dialog-actions"><button class="ghost" type="button" data-release-back>Back</button><button type="submit" class="primary">Save</button></div></form>`;
    el('releaseForm').onsubmit=async event=>{event.preventDefault();if(busy)return;busy=true;const button=event.target.querySelector('[type=submit]');button.disabled=true;try{await action(event.target);await load();}catch(error){if(el('releaseError'))el('releaseError').textContent=error.message;}finally{busy=false;button.disabled=false;}};
  }
  content().onclick=async event=>{
    if(busy)return;
    try{
      if(event.target.closest('[data-release-back]'))return await load();
      if(event.target.closest('[data-release-new]'))form('Record a versioned release',field('version_label','Release version')+field('image','Immutable image reference (registry/path@sha256:…)')+area('release_notes','Customer-facing release notes')+area('migration_notes','Database changes')+area('rollback_notes','Rollback requirements')+`<fieldset><legend>Include fixes</legend><p class="muted">Classify fixes in Bot care first. Customer ticket contents are not copied into release notes.</p>${issues.filter(i=>i.scope!=='UNDECIDED').map(i=>`<label class="check"><input name="issue_ids" value="${esc(i.id)}" type="checkbox">${esc(i.title)} · ${esc(i.scope)}</label>`).join('')||'<p>No classified issues yet. A feature release can be recorded without incidents.</p>'}</fieldset>`,async form=>{const values=Object.fromEntries(new FormData(form));values.issue_ids=new FormData(form).getAll('issue_ids');await api(base+'/releases',{method:'POST',body:JSON.stringify(values)});});
      const button=event.target.closest('[data-release-update]');if(button){const row=rows.find(r=>r.id===button.dataset.releaseUpdate);form(`Plan update to ${row.version_label}`,`<label>Issue being fixed<select name="issue_id">${row.issue_ids.map(id=>`<option value="${esc(id)}">${esc(issues.find(i=>i.id===id)?.title||id)}</option>`).join('')}</select></label>`+field('previous_image','Currently installed immutable image')+'<p class="muted">This creates a proposal. The customer owner approves it and records backup/install evidence in Bot care & updates.</p>',async form=>{await api(base+'/updates',{method:'POST',body:JSON.stringify({...Object.fromEntries(new FormData(form)),release_id:row.id,version_label:row.version_label,image:row.image,release_notes:row.release_notes,migration_notes:row.migration_notes})});});}
    }catch(error){content().insertAdjacentHTML('afterbegin',`<p role="alert" class="signin-error">${esc(error.message)}</p>`);}
  };
  el('openReleases').onclick=()=>{el('releasesDialog').showModal();load().catch(error=>{content().textContent=error.message;});};
  el('closeReleases').onclick=()=>el('releasesDialog').close();
  el('releasesDialog').addEventListener('close',()=>{content().replaceChildren();rows=[];issues=[];});
  return {load};
})();
