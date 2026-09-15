const tg = window.Telegram?.WebApp;
const state = { token: null, botId: null, bootstrap: null, categories: [], products: [], orders: [], jobs: [], events: [], providerCapabilities: null, suppliers: [], paymentProviders: [], mappings: [], members: [], financialCases: [], analytics: null, auditLogs: [], auditNextOffset: null, bots: [], botJobs: [], botTemplates: [], botWizardStep: 1, botWizardMode: "create" };
const el = (id) => document.getElementById(id);
const escapeHtml = (v) => String(v ?? "").replace(/[&<>'"]/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[c]));

async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  const response = await fetch(path, { ...options, headers });
  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try { const parsed=(await response.json()).detail; detail=typeof parsed==="string"?parsed:(parsed?.message||JSON.stringify(parsed)||detail); } catch (_) {}
    throw new Error(detail);
  }
  if (response.status === 204) return null;
  return response.json();
}

async function authenticate() {
  const params = new URLSearchParams(location.search);
  state.botId = params.get("bot_id");
  if (!state.botId) throw new Error("Missing bot_id in admin launch URL.");
  if (!tg?.initData) throw new Error("Open this admin console from Telegram so the session can be verified.");
  tg.ready(); tg.expand();
  const auth = await api("/api/v1/auth/telegram-miniapp", {
    method: "POST",
    body: JSON.stringify({ init_data: tg.initData, bot_id: state.botId }),
  });
  state.token = auth.access_token;
}

function money(amount, currency) { return `${Number(amount).toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2})} ${currency}`; }
function statusChip(value) { const danger = ["FAILED","DEAD_LETTER","MANUAL_REVIEW"].includes(value); const ok=["FULFILLED","COMPLETED","PROCESSED"].includes(value); return `<span class="chip ${danger?"danger":ok?"ok":""}">${escapeHtml(value || "—")}</span>`; }
function parseJson(id){try{return JSON.parse(el(id).value||"{}");}catch(_){throw new Error(`Invalid JSON in ${id}.`);}}

async function loadBootstrap() {
  state.bootstrap = await api("/api/v1/admin/bootstrap");
  el("storeName").textContent = state.bootstrap.store.name;
  el("actorRole").textContent = state.bootstrap.actor.role;
  el("actorName").textContent = [state.bootstrap.actor.first_name,state.bootstrap.actor.last_name].filter(Boolean).join(" ") || state.bootstrap.actor.username || "Staff";
  const c = state.bootstrap.counts;
  el("metricGrid").innerHTML = [
    ["Products", c.products, `${c.active_products} active`],
    ["Orders", c.orders, `${c.attention_orders} need attention`],
    ["Dead letters", c.dead_letter_jobs, "Fulfillment queue"],
    ["Financial cases", c.financial_open_cases, `${c.reconciliation_reviews} provider review event${c.reconciliation_reviews===1?"":"s"}`],
  ].map(([label,value,hint])=>`<article class="metric"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong><small class="muted">${escapeHtml(hint)}</small></article>`).join("");
  el("newProduct").classList.toggle("hidden", state.bootstrap.actor.role === "STAFF");
  document.querySelectorAll("[data-admin-only]").forEach(node=>node.classList.toggle("hidden",!["ADMIN","OWNER"].includes(state.bootstrap.actor.role)));
}

function canOperateBots(){return ["ADMIN","OWNER"].includes(state.bootstrap?.actor?.role);}

async function loadBots(){
  const [fleet,jobs,templates]=await Promise.all([
    api("/api/v1/admin/bots/fleet"),
    api("/api/v1/admin/bots/jobs?limit=50"),
    api("/api/v1/admin/bots/templates"),
  ]);
  state.bots=fleet.bots;state.botJobs=jobs.jobs;state.botTemplates=templates.templates;
  el("botList").innerHTML=state.bots.length?state.bots.map(b=>`<article class="item"><div class="item-row"><div><h3>${escapeHtml(b.display_name)} ${b.username?`<span class="chip">@${escapeHtml(b.username)}</span>`:""}</h3><div class="item-meta"><span>Telegram ${escapeHtml(b.telegram_bot_id)}</span><span>Credential v${escapeHtml(b.credential_version)} · ${escapeHtml(b.credential_status)}</span><span>Runtime ${escapeHtml(b.runtime_status)}</span><span>Channel ${escapeHtml(b.release_channel)}</span><span>${b.template_key?`${escapeHtml(b.template_key)} v${escapeHtml(b.template_version||1)}`:"legacy config"}</span><span>Updated ${new Date(b.updated_at).toLocaleString()}</span></div>${b.runtime_detail?`<p class="muted compact">Runtime: ${escapeHtml(b.runtime_detail)}</p>`:""}</div><div class="ops-actions">${statusChip(b.desired_state)} ${statusChip(b.runtime_status)} ${statusChip(b.credential_status)}${canOperateBots()?`<button class="ghost" data-verify-bot-credential="${b.id}">Verify credential</button><button class="ghost" data-rotate-bot-credential="${b.id}">Rotate token</button><button class="ghost" data-launch-check-bot="${b.id}">Launch check</button><button class="ghost" data-restart-bot="${b.id}" ${b.is_enabled?"":"disabled"}>Restart runtime</button><button class="ghost" data-release-channel-bot="${b.id}" data-release-channel="${b.release_channel}">${b.release_channel==="CANARY"?"Promote stable":"Move to canary"}</button><button class="ghost" data-configure-bot="${b.id}">Configure</button><button class="ghost" data-toggle-bot="${b.id}" data-enabled="${b.is_enabled}">${b.is_enabled?"Disable":"Enable"}</button>`:""}</div></div></article>`).join(""):`<div class="empty">No bots provisioned for this tenant.</div>`;
  el("botJobList").innerHTML=state.botJobs.length?state.botJobs.map(j=>`<article class="item"><div class="item-row"><div><h3>${escapeHtml(j.expected_username?`@${j.expected_username}`:(j.requested_display_name||"Bot provisioning"))}</h3><div class="item-meta"><span>Attempt ${j.attempt_count}/${j.max_attempts}</span><span>${j.verified_username?`Verified @${escapeHtml(j.verified_username)}`:"Identity pending"}</span><span>${new Date(j.created_at).toLocaleString()}</span></div><p class="muted compact">${escapeHtml(j.last_error_code||"Durable job awaiting or completed verification.")}</p></div><div class="ops-actions">${statusChip(j.status)}${canOperateBots()&&j.status==="FAILED"?`<button class="ghost" data-retry-bot-job="${j.id}">Retry</button>`:""}${canOperateBots()&&["PENDING","RETRY"].includes(j.status)?`<button class="ghost" data-cancel-bot-job="${j.id}">Cancel</button>`:""}</div></div></article>`).join(""):`<div class="empty">No provisioning jobs yet.</div>`;
}

function selectedBotTemplate(){return state.botTemplates.find(t=>t.key===el("botTemplate").value)||state.botTemplates[0]||null;}
function setWizardStep(step){
  state.botWizardStep=Math.max(1,Math.min(3,step));
  document.querySelectorAll("[data-wizard-step]").forEach(n=>n.classList.toggle("hidden",Number(n.dataset.wizardStep)!==state.botWizardStep));
  document.querySelectorAll("[data-step-dot]").forEach(n=>n.classList.toggle("active",Number(n.dataset.stepDot)===state.botWizardStep));
  el("botWizardBack").classList.toggle("hidden",state.botWizardStep===1);
  const last=state.botWizardStep===3;
  el("botWizardNext").classList.toggle("hidden",last);
  el("botWizardSubmit").classList.toggle("hidden",!last);
  el("botWizardSubmit").textContent=state.botWizardMode==="edit"?"Save configuration":"Queue provisioning";
}
function applyBotTemplateDefaults(template,{preserveIdentity=false}={}){
  if(!template)return;const c=template.default_config||{},b=c.branding||{};
  el("botCurrency").value=c.currency||"USD";el("botLocale").value=c.locale||"en";el("botBrandAccent").value=b.brand_accent||"#7c6cff";el("botStoreTagline").value=b.store_tagline||"";el("botWelcomeText").value=b.welcome_text||"";el("botLogoUrl").value=b.brand_logo_url||"";el("botSupportContact").value=b.support_contact||"";el("botSupportUrl").value=b.support_url||"";el("botMenuText").value=b.menu_text||"Open Store";
  const mods=new Set(c.enabled_modules||[]);el("botModuleCatalog").checked=mods.has("catalog");el("botModuleOrders").checked=mods.has("orders");el("botModuleAccount").checked=mods.has("account");
  if(!preserveIdentity&&!el("botDisplayName").value)el("botDisplayName").value=template.name;
  renderBotTemplatePreview();renderBotBrandPreview();
}
function renderBotTemplatePreview(){const t=selectedBotTemplate();el("botTemplatePreview").innerHTML=t?`<strong>${escapeHtml(t.name)} · v${escapeHtml(t.version)}</strong><span>${escapeHtml(t.description)}</span><span>${escapeHtml(t.recommended_for)}</span>`:"<span>No template available.</span>";}
function renderBotBrandPreview(){const name=el("botDisplayName").value.trim()||selectedBotTemplate()?.name||"My Store";const tagline=el("botStoreTagline").value.trim()||"Your storefront preview";const accent=el("botBrandAccent").value||"#7c6cff";const logo=el("botLogoUrl").value.trim();const mark=el("botBrandPreviewMark");mark.style.backgroundColor=accent;mark.style.backgroundImage=logo?`url("${logo.replaceAll('"','%22')}")`:"";mark.textContent=logo?"":(name.charAt(0).toUpperCase()||"G");el("botBrandPreviewName").textContent=name;el("botBrandPreviewTagline").textContent=tagline;}
function templateKeyForBot(bot){return bot?.template_key||bot?.config?._factory?.template_key||"general-commerce";}
function openBotProvision(bot=null){
  state.botWizardMode=bot?"edit":"create";el("botWizardBotId").value=bot?.id||"";el("botWizardTitle").textContent=bot?"Configure bot":"Create a bot";
  el("botTemplate").innerHTML=state.botTemplates.map(t=>`<option value="${escapeHtml(t.key)}">${escapeHtml(t.name)} · v${escapeHtml(t.version)}</option>`).join("");
  const key=templateKeyForBot(bot);if(key&&state.botTemplates.some(t=>t.key===key))el("botTemplate").value=key;
  el("botToken").value="";el("botExpectedUsername").value=bot?.username||"";el("botDisplayName").value=bot?.display_name||"";el("botEnabled").checked=bot?.is_enabled??true;
  el("botCredentialFields").classList.toggle("hidden",Boolean(bot));el("botCredentialPreserved").classList.toggle("hidden",!bot);el("botToken").required=!bot;
  applyBotTemplateDefaults(selectedBotTemplate(),{preserveIdentity:Boolean(bot)});
  if(bot){const c=bot.config||{},b=c.branding||{};el("botCurrency").value=c.currency||el("botCurrency").value;el("botLocale").value=c.locale||el("botLocale").value;el("botBrandAccent").value=b.brand_accent||el("botBrandAccent").value;el("botStoreTagline").value=b.store_tagline||"";el("botWelcomeText").value=b.welcome_text||"";el("botLogoUrl").value=b.brand_logo_url||"";el("botSupportContact").value=b.support_contact||"";el("botSupportUrl").value=b.support_url||"";el("botMenuText").value=b.menu_text||"Open Store";const mods=new Set(c.enabled_modules||[]);el("botModuleCatalog").checked=mods.has("catalog");el("botModuleOrders").checked=mods.has("orders");el("botModuleAccount").checked=mods.has("account");}
  renderBotBrandPreview();setWizardStep(1);el("botProvisionDialog").showModal();
}
function botWizardPayload(){const template=selectedBotTemplate();if(!template)throw new Error("Select a bot template.");const modules=[["catalog","botModuleCatalog"],["orders","botModuleOrders"],["account","botModuleAccount"]].filter(([,id])=>el(id).checked).map(([name])=>name);if(!modules.length)throw new Error("Enable at least one bot module.");return {display_name:el("botDisplayName").value.trim(),template_key:template.key,template_version:template.version,currency:el("botCurrency").value.trim().toUpperCase(),locale:el("botLocale").value.trim(),branding:{brand_accent:el("botBrandAccent").value,store_tagline:el("botStoreTagline").value.trim(),welcome_text:el("botWelcomeText").value.trim(),brand_logo_url:el("botLogoUrl").value.trim(),support_contact:el("botSupportContact").value.trim(),support_url:el("botSupportUrl").value.trim(),menu_text:el("botMenuText").value.trim(),store_button_text:`🛍️ ${el("botMenuText").value.trim()||"Open Store"}`},enabled_modules:modules};}
async function saveBotProvision(event){
  event.preventDefault();const payload=botWizardPayload();const botId=el("botWizardBotId").value;
  if(state.botWizardMode==="edit"&&botId){await api(`/api/v1/admin/bots/${botId}/configuration`,{method:"PATCH",body:JSON.stringify(payload)});}
  else{const key=crypto.randomUUID?.()||`bot-${Date.now()}-${Math.random()}`;await api("/api/v1/admin/bots/provision",{method:"POST",headers:{"Idempotency-Key":key},body:JSON.stringify({...payload,bot_token:el("botToken").value.trim(),expected_username:el("botExpectedUsername").value.trim()||null,is_enabled:el("botEnabled").checked})});}
  el("botProvisionDialog").close();await loadBots();
}
async function toggleBot(id,enabled){if(!confirm(`${enabled?"Disable":"Enable"} this bot? Runtime reconciliation will apply the desired state automatically.`))return;await api(`/api/v1/admin/bots/${id}/state`,{method:"PATCH",body:JSON.stringify({is_enabled:!enabled})});await loadBots();}
async function verifyBotCredential(id){await api(`/api/v1/admin/bots/${id}/credentials/verify`,{method:"POST"});await loadBots();}
function openBotCredentialRotation(id){el("botCredentialBotId").value=id;el("botCredentialToken").value="";el("botCredentialDialog").showModal();}
async function saveBotCredentialRotation(event){event.preventDefault();const id=el("botCredentialBotId").value;const token=el("botCredentialToken").value.trim();if(!token)throw new Error("Paste the new BotFather token.");await api(`/api/v1/admin/bots/${id}/credentials/rotate`,{method:"POST",body:JSON.stringify({bot_token:token})});el("botCredentialToken").value="";el("botCredentialDialog").close();await loadBots();}
async function restartBotRuntime(id){if(!confirm("Restart this bot runtime instance? Active polling will reconnect automatically."))return;await api(`/api/v1/admin/bots/${id}/runtime/restart`,{method:"POST"});await loadBots();}
async function toggleBotReleaseChannel(id,current){const next=current==="CANARY"?"STABLE":"CANARY";if(!confirm(`Move this bot from ${current} to ${next}? The matching runtime pool will take ownership automatically.`))return;await api(`/api/v1/admin/bots/${id}/release-channel`,{method:"PATCH",body:JSON.stringify({release_channel:next})});await loadBots();}
async function openBotLaunchCheck(id){const data=await api(`/api/v1/admin/bots/${id}/launch-readiness`);el("botLaunchSummary").innerHTML=data.launchable?`<strong class="ok-text">Launchable.</strong><span class="muted"> All blocking checks passed.</span>`:`<strong class="danger-text">Not launchable yet.</strong><span class="muted"> Resolve the blocking checks below.</span>`;el("botLaunchChecks").innerHTML=data.checks.map(c=>`<article class="item"><div class="item-row"><div><h3>${escapeHtml(c.label)}</h3><p class="muted compact">${escapeHtml(c.detail)}</p></div><div>${statusChip(c.status)}</div></div></article>`).join("");el("botLaunchDialog").showModal();}
async function retryBotJob(id){await api(`/api/v1/admin/bots/jobs/${id}/retry`,{method:"POST"});await loadBots();}
async function cancelBotJob(id){if(!confirm("Cancel this queued provisioning job?"))return;await api(`/api/v1/admin/bots/jobs/${id}/cancel`,{method:"POST"});await loadBots();}

async function loadAnalytics(){
  const days=el("analyticsDays").value||"30";
  state.analytics=await api(`/api/v1/admin/analytics/overview?days=${encodeURIComponent(days)}`);
  const a=state.analytics;
  const rate=a.fulfillment.success_rate==null?"—":`${a.fulfillment.success_rate}%`;
  el("analyticsMetricGrid").innerHTML=[
    ["Orders",a.orders.total,`${a.orders.fulfilled} fulfilled · ${a.orders.refunded} refunded`],
    ["Fulfillment",rate,`${a.fulfillment.succeeded}/${a.fulfillment.attempts} attempts succeeded`],
    ["Dead letters",a.operations.dead_letter_jobs,"Current queue exposure"],
    ["Financial cases",a.operations.open_financial_cases,"Open + in progress"],
    ["Frozen wallets",a.operations.frozen_wallets,"Current financial holds"],
    ["Audit events",a.operations.audit_events,`${a.period.days}-day operator activity`],
  ].map(([label,value,hint])=>`<article class="metric"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong><small class="muted">${escapeHtml(hint)}</small></article>`).join("");
  el("analyticsFinancials").innerHTML=a.financials.length?a.financials.map(f=>`<article class="panel analytics-currency"><div class="item-row"><h3>${escapeHtml(f.currency)}</h3><span class="chip">${f.order_count} orders</span></div><div class="finance-grid"><div><small>Gross order value</small><strong>${escapeHtml(money(f.gross_order_value,f.currency))}</strong></div><div><small>Net order value</small><strong>${escapeHtml(money(f.net_order_value,f.currency))}</strong></div><div><small>Top-ups</small><strong>${escapeHtml(money(f.topup_value,f.currency))}</strong></div><div><small>Reversals</small><strong>${escapeHtml(money(f.reversal_value,f.currency))}</strong></div><div><small>Wallet liability</small><strong>${escapeHtml(money(f.wallet_liability,f.currency))}</strong></div><div><small>Wallets</small><strong>${escapeHtml(f.wallet_count)}</strong></div></div></article>`).join(""):`<div class="empty">No financial activity in this period.</div>`;
  el("providerAnalytics").innerHTML=a.providers.length?a.providers.map(p=>`<article class="item"><div class="item-row"><div><h3>${escapeHtml(p.provider_name)}</h3><div class="item-meta"><span>${p.attempts} attempts</span><span>${p.succeeded} succeeded</span><span>${p.success_rate==null?"—":`${p.success_rate}%`} success</span><span>${escapeHtml(money(p.cost_amount,p.currency))} cost</span></div></div>${statusChip(p.non_succeeded?"ATTENTION":"HEALTHY")}</div></article>`).join(""):`<div class="empty">No fulfillment attempts in this period.</div>`;
  el("dailyAnalytics").innerHTML=a.daily.length?a.daily.slice(-45).reverse().map(d=>`<article class="item compact-item"><div class="item-row"><div><strong>${escapeHtml(d.date)}</strong><div class="item-meta"><span>${escapeHtml(d.currency)}</span><span>${d.order_count} orders · ${escapeHtml(money(d.order_value,d.currency))}</span><span>${d.topup_count} top-ups · ${escapeHtml(money(d.topup_value,d.currency))}</span></div></div></div></article>`).join(""):`<div class="empty">No daily activity in this period.</div>`;
}

async function loadAuditLogs(offset=0){
  const params=new URLSearchParams();const q=el("auditSearch").value.trim();const action=el("auditAction").value.trim();const resourceType=el("auditResourceType").value.trim();
  if(q)params.set("q",q);if(action)params.set("action",action);if(resourceType)params.set("resource_type",resourceType);params.set("offset",String(offset));params.set("limit","50");
  const data=await api(`/api/v1/admin/audit-logs?${params}`);state.auditLogs=offset?[...state.auditLogs,...data.logs]:data.logs;state.auditNextOffset=data.next_offset;
  el("auditList").innerHTML=state.auditLogs.length?state.auditLogs.map(log=>`<article class="item"><div class="item-row"><div><h3>${escapeHtml(log.action)}</h3><div class="item-meta"><span>${escapeHtml(log.resource_type)}</span><span>${escapeHtml(log.resource_id)}</span><span>${escapeHtml(log.actor.display_name)}</span><span>${new Date(log.created_at).toLocaleString()}</span></div><pre class="audit-details">${escapeHtml(JSON.stringify(log.details,null,2))}</pre></div></div></article>`).join(""):`<div class="empty">No audit records match this filter.</div>`;
  el("auditCount").textContent=`Showing ${state.auditLogs.length} of ${data.total} records`;el("auditLoadMore").classList.toggle("hidden",data.next_offset==null);
}

async function loadCategories() {
  state.categories = await api("/api/v1/admin/catalog/categories");
  el("productCategory").innerHTML = `<option value="">Uncategorized</option>` + state.categories.map(c=>`<option value="${c.id}">${escapeHtml(c.name)}</option>`).join("");
}

async function loadProducts() {
  const params = new URLSearchParams();
  const q=el("productSearch").value.trim(); const active=el("productActive").value;
  if(q) params.set("q",q); if(active) params.set("active",active);
  const data = await api(`/api/v1/admin/catalog/products?${params}`);
  state.products = data.products;
  el("productList").innerHTML = data.products.length ? data.products.map(p=>`<article class="item"><div class="item-row"><div><h3>${escapeHtml(p.title)}</h3><div class="item-meta"><span>${escapeHtml(p.category_name||"Uncategorized")}</span><span>${p.variants.length} variant${p.variants.length===1?"":"s"}</span><span>${p.variants.reduce((n,v)=>n+Number(v.stock_quantity),0)} stock</span></div></div><div>${statusChip(p.is_active?"ACTIVE":"INACTIVE")} ${state.bootstrap.actor.role!=="STAFF"?`<button class="ghost" data-edit-product="${p.id}">Edit</button>`:""}</div></div></article>`).join("") : `<div class="empty">No products match this filter.</div>`;
}

async function loadOrders() {
  const params=new URLSearchParams(); const q=el("orderSearch").value.trim(); const s=el("orderStatus").value;
  if(q) params.set("q",q); if(s) params.set("status",s);
  const data=await api(`/api/v1/admin/orders?${params}`); state.orders=data.orders;
  el("orderList").innerHTML=data.orders.length?data.orders.map(o=>`<article class="item"><div class="item-row"><div><h3>${escapeHtml(o.order_number)}</h3><div class="item-meta"><span>${escapeHtml(o.customer_name)}</span><span>${o.items.reduce((n,i)=>n+i.quantity,0)} units</span><span>${escapeHtml(money(o.total_amount,o.currency))}</span><span>${new Date(o.created_at).toLocaleString()}</span></div></div><div>${statusChip(o.status)} ${statusChip(o.fulfillment_status)}</div></div></article>`).join(""):`<div class="empty">No orders found.</div>`;
}

async function loadEvents() {
  const v=el("reviewFilter").value; const params=new URLSearchParams(); if(v) params.set("requires_review",v);
  state.events=await api(`/api/v1/admin/reconciliation-events?${params}`);
  el("reconciliationList").innerHTML=state.events.length?state.events.map(e=>`<article class="item"><div class="item-row"><div><h3>${escapeHtml(e.classification)}</h3><div class="item-meta"><span>${escapeHtml(e.provider)}</span><span>${escapeHtml(e.event_type)}</span><span>${escapeHtml(money(e.amount,e.currency))}</span><span>${escapeHtml(e.provider_event_id)}</span></div></div><div>${statusChip(e.status)} ${e.requires_review?'<span class="chip danger">REVIEW</span>':''}</div></div></article>`).join(""):`<div class="empty">No reconciliation events for this filter.</div>`;
}

function canResolveFinance(){return ["ADMIN","OWNER"].includes(state.bootstrap?.actor?.role);}

async function loadFinancialCases(){
  const params=new URLSearchParams(); const q=el("financeSearch").value.trim(); const st=el("financeStatus").value; const sev=el("financeSeverity").value; const assigned=el("financeAssigned").value;
  if(q)params.set("q",q); if(st)params.set("status",st); if(sev)params.set("severity",sev); if(assigned)params.set("assigned",assigned);
  const data=await api(`/api/v1/admin/financial-resolution/cases?${params}`); state.financialCases=data.cases;
  el("financialCaseList").innerHTML=data.cases.length?data.cases.map(c=>{
    const wallet=c.wallet_id?`${money(c.wallet_balance,c.wallet_currency)} · ${c.wallet_active?"ACTIVE":"FROZEN"}`:"No wallet impact";
    const mine=c.assigned_to_user_id===state.bootstrap.actor.id;
    const claimButton=c.status!=="RESOLVED"&&!c.assigned_to_user_id?`<button class="ghost" data-claim-finance="${c.id}">Claim</button>`:"";
    const releaseButton=c.status!=="RESOLVED"&&mine?`<button class="ghost" data-release-finance="${c.id}">Release</button>`:"";
    const resolveButton=c.status!=="RESOLVED"&&canResolveFinance()&&c.available_actions.length?`<button class="primary" data-resolve-finance="${c.id}">Resolve</button>`:"";
    return `<article class="item"><div class="item-row"><div><h3>${escapeHtml(c.case_type)}</h3><div class="item-meta"><span>${escapeHtml(c.severity)}</span><span>${escapeHtml(c.provider||"internal")}</span><span>${escapeHtml(wallet)}</span><span>v${c.version}</span></div><p class="muted compact">${escapeHtml(c.reversal_error_detail||c.event_classification||c.resolution_code||"Financial evidence requires operator review.")}</p></div><div class="ops-actions">${statusChip(c.status)} ${c.wallet_active===false?'<span class="chip danger">WALLET FROZEN</span>':''}${claimButton}${releaseButton}${resolveButton}</div></div></article>`;
  }).join(""):`<div class="empty">No financial resolution cases for this filter.</div>`;
}

async function claimFinancialCase(id){const c=state.financialCases.find(x=>x.id===id);if(!c)return;await api(`/api/v1/admin/financial-resolution/cases/${id}/claim`,{method:"POST",body:JSON.stringify({expected_version:c.version})});await Promise.all([loadFinancialCases(),loadBootstrap()]);}
async function releaseFinancialCase(id){const c=state.financialCases.find(x=>x.id===id);if(!c)return;await api(`/api/v1/admin/financial-resolution/cases/${id}/release`,{method:"POST",body:JSON.stringify({expected_version:c.version})});await loadFinancialCases();}
function openFinancialResolution(id){const c=state.financialCases.find(x=>x.id===id);if(!c)return;el("financialCaseId").value=c.id;el("financialCaseVersion").value=c.version;el("financialCaseTitle").textContent=c.case_type;el("financialCaseSummary").textContent=`${c.severity} · ${c.wallet_id?(c.wallet_active?"wallet active":"wallet frozen"):"no wallet impact"}`;el("financialAction").innerHTML=c.available_actions.map(a=>`<option value="${escapeHtml(a)}">${escapeHtml(a.replaceAll("_"," "))}</option>`).join("");el("financialNote").value="";el("financialResolutionDialog").showModal();}
async function resolveFinancialCase(event){event.preventDefault();const id=el("financialCaseId").value;await api(`/api/v1/admin/financial-resolution/cases/${id}/resolve`,{method:"POST",body:JSON.stringify({expected_version:Number(el("financialCaseVersion").value),action:el("financialAction").value,note:el("financialNote").value.trim()})});el("financialResolutionDialog").close();await Promise.all([loadFinancialCases(),loadBootstrap(),loadEvents()]);}

function canOperateFulfillment() { return ["ADMIN","OWNER"].includes(state.bootstrap?.actor?.role); }

async function loadFulfillment() {
  const params=new URLSearchParams(); const q=el("fulfillmentSearch").value.trim(); const s=el("fulfillmentStatus").value;
  if(q) params.set("q",q); if(s) params.set("status",s);
  const data=await api(`/api/v1/admin/fulfillment/jobs?${params}`); state.jobs=data.jobs;
  el("fulfillmentList").innerHTML=data.jobs.length?data.jobs.map(j=>`<article class="item"><div class="item-row"><div><h3>${escapeHtml(j.order_number)}</h3><div class="item-meta"><span>Attempt ${j.attempt_number}</span><span>${escapeHtml(j.failure_classification||"No classification")}</span><span>${new Date(j.updated_at).toLocaleString()}</span><span>${j.manual_requeue_count} manual requeue${j.manual_requeue_count===1?"":"s"}</span></div><p class="muted compact">${escapeHtml(j.last_error||j.requeue_reason)}</p></div><div class="ops-actions">${statusChip(j.order_status)} ${statusChip(j.status)}${canOperateFulfillment()?`<button class="ghost" data-reconcile-order="${j.order_id}">Reconcile</button><button class="primary" data-requeue-job="${j.id}" ${j.can_requeue?"":"disabled"}>Requeue</button>`:""}</div></div>${j.can_requeue?`<small class="ok-text">Safe requeue check passed.</small>`:`<small class="danger-text">${escapeHtml(j.requeue_reason)}</small>`}</article>`).join(""):`<div class="empty">No fulfillment jobs for this filter.</div>`;
}

async function requeueJob(jobId){
  if(!confirm("Requeue this dead-letter job? The server will re-check refund and provider-state safety invariants.")) return;
  await api(`/api/v1/admin/fulfillment/jobs/${jobId}/requeue`,{method:"POST"});
  await Promise.all([loadFulfillment(),loadBootstrap()]);
}

async function reconcileOrder(orderId){
  const result=await api(`/api/v1/admin/fulfillment/orders/${orderId}/reconcile`,{method:"POST"});
  const summary=result.results.length?result.results.map(r=>`${r.issue_type}: ${r.action_taken}`).join("\n"):"No active fulfillment attempt required reconciliation.";
  alert(summary); await Promise.all([loadFulfillment(),loadBootstrap(),loadOrders()]);
}

function canConfigureProviders(){return ["ADMIN","OWNER"].includes(state.bootstrap?.actor?.role);}

async function loadProviderCapabilities(){
  state.providerCapabilities=await api("/api/v1/admin/provider-capabilities");
  el("supplierType").innerHTML=state.providerCapabilities.supplier_provider_types.map(v=>`<option value="${escapeHtml(v)}">${escapeHtml(v)}</option>`).join("");
  el("paymentProviderName").innerHTML=state.providerCapabilities.payment_provider_names.map(v=>`<option value="${escapeHtml(v)}">${escapeHtml(v)}</option>`).join("");
}

async function loadSuppliers(){
  state.suppliers=await api("/api/v1/admin/providers");
  el("supplierProviderList").innerHTML=state.suppliers.length?state.suppliers.map(p=>`<article class="item"><div class="item-row"><div><h3>${escapeHtml(p.name)}</h3><div class="item-meta"><span>${escapeHtml(p.provider_type)}</span><span>Priority ${p.priority}</span><span>${p.mapping_count} mapping${p.mapping_count===1?"":"s"}</span><span>${p.credentials.length?`${p.credentials.length} credential ref${p.credentials.length===1?"":"s"}`:"No credentials"}</span></div></div><div class="ops-actions">${statusChip(p.health_status)} ${statusChip(p.is_enabled?"ENABLED":"DISABLED")}${canConfigureProviders()?`<button class="ghost" data-health-provider="${p.id}">Health</button><button class="ghost" data-toggle-provider="${p.id}">${p.is_enabled?"Disable":"Enable"}</button>`:""}</div></div></article>`).join(""):`<div class="empty">No fulfillment providers configured.</div>`;
  el("mappingProvider").innerHTML=state.suppliers.map(p=>`<option value="${p.id}">${escapeHtml(p.name)}</option>`).join("");
}

async function loadPaymentProviders(){
  state.paymentProviders=await api("/api/v1/admin/payment-providers");
  el("paymentProviderList").innerHTML=state.paymentProviders.length?state.paymentProviders.map(p=>`<article class="item"><div class="item-row"><div><h3>${escapeHtml(p.settings?.display_name||p.provider_name)}</h3><div class="item-meta"><span>${escapeHtml(p.provider_name)}</span><span>Credentials ${p.credentials_configured?"configured":"missing"}</span><span>Webhook ${p.webhook_secret_configured?"configured":"none"}</span></div></div><div class="ops-actions">${statusChip(p.is_enabled?"ENABLED":"DISABLED")}${canConfigureProviders()?`<button class="ghost" data-edit-payment-provider="${escapeHtml(p.provider_name)}">Edit</button>`:""}</div></div></article>`).join(""):`<div class="empty">No payment providers configured.</div>`;
}

async function loadMappings(){
  state.mappings=await api("/api/v1/admin/provider-mappings");
  el("providerMappingList").innerHTML=state.mappings.length?state.mappings.map(m=>`<article class="item"><div class="item-row"><div><h3>${escapeHtml(m.product_title)}${m.variant_title?` · ${escapeHtml(m.variant_title)}`:""}</h3><div class="item-meta"><span>${escapeHtml(m.provider_name)}</span><span>External: ${escapeHtml(m.external_product_id)}</span><span>Cost ${escapeHtml(money(m.cost_price,m.cost_currency))}</span><span>${m.priority_override?`Route priority ${m.priority_override}`:"Provider priority"}</span></div></div>${statusChip(m.is_enabled?"ENABLED":"DISABLED")}</div></article>`).join(""):`<div class="empty">No supplier product mappings configured.</div>`;
}

async function loadProviderOps(){
  if(!state.providerCapabilities) await loadProviderCapabilities();
  if(!state.products.length) await loadProducts();
  await Promise.all([loadSuppliers(),loadPaymentProviders(),loadMappings()]);
  const locked=!canConfigureProviders(); ["newSupplier","configurePayment","newMapping"].forEach(id=>el(id).classList.toggle("hidden",locked));
  el("mappingProduct").innerHTML=state.products.map(p=>`<option value="${p.id}">${escapeHtml(p.title)}</option>`).join("");
  refreshMappingVariants();
}

function canManageMember(member){
  const actor=state.bootstrap?.actor?.role;if(actor==="OWNER")return true;if(actor!=="ADMIN")return false;return !["ADMIN","OWNER"].includes(member.role);
}

async function loadMembers(){
  if(!["ADMIN","OWNER"].includes(state.bootstrap?.actor?.role))return;const params=new URLSearchParams();const q=el("memberSearch").value.trim();const role=el("memberRole").value;const active=el("memberActive").value;if(q)params.set("q",q);if(role)params.set("role",role);if(active)params.set("active",active);const data=await api(`/api/v1/admin/members?${params}`);state.members=data.members;el("memberList").innerHTML=data.members.length?data.members.map(m=>{const name=[m.first_name,m.last_name].filter(Boolean).join(" ")||m.username||m.email||"Unnamed member";return `<article class="item"><div class="item-row"><div><h3>${escapeHtml(name)}${m.is_self?' <span class="chip">YOU</span>':''}</h3><div class="item-meta"><span>${escapeHtml(m.username?`@${m.username}`:"No username")}</span><span>${escapeHtml(m.email||"No email")}</span><span>${m.permissions.length} permission${m.permissions.length===1?"":"s"}</span><span>Joined ${new Date(m.created_at).toLocaleDateString()}</span></div></div><div class="ops-actions">${statusChip(m.role)} ${statusChip(m.is_active?"ACTIVE":"INACTIVE")}${canManageMember(m)?`<button class="ghost" data-edit-member="${m.id}">Manage</button>`:""}</div></div></article>`}).join(""):`<div class="empty">No members match this filter.</div>`;
}

function openMember(member){
  el("memberId").value=member.id;el("memberDialogTitle").textContent=`Manage ${member.username?`@${member.username}`:"member"}`;el("memberIdentity").textContent=[member.first_name,member.last_name,member.email].filter(Boolean).join(" · ")||"Tenant membership";el("memberEditRole").innerHTML=(state.bootstrap.actor.role==="OWNER"?["OWNER","ADMIN","MANAGER","STAFF","CUSTOMER"]:["MANAGER","STAFF","CUSTOMER"]).map(role=>`<option value="${role}">${role}</option>`).join("");el("memberEditRole").value=member.role;el("memberEditActive").checked=member.is_active;el("memberPermissions").value=(member.permissions||[]).join("\n");el("memberDialog").showModal();
}

async function saveMember(event){
  event.preventDefault();const id=el("memberId").value;const current=state.members.find(m=>m.id===id);if(!current)throw new Error("Member is no longer in the current result set.");const active=el("memberEditActive").checked;if(current.is_active&&!active&&!confirm("Deactivate this tenant membership? Existing access will be rejected on the next API request."))return;const permissions=el("memberPermissions").value.split(/\n|,/).map(v=>v.trim()).filter(Boolean);await api(`/api/v1/admin/members/${id}`,{method:"PATCH",body:JSON.stringify({role:el("memberEditRole").value,is_active:active,permissions})});el("memberDialog").close();await loadMembers();
}

function refreshMappingVariants(){
  const product=state.products.find(p=>p.id===el("mappingProduct").value);
  el("mappingVariant").innerHTML=`<option value="">Product-level mapping</option>`+(product?.variants||[]).map(v=>`<option value="${v.id}">${escapeHtml(v.title)} · ${escapeHtml(v.sku)}</option>`).join("");
}

function openSupplier(){
  el("supplierName").value="";el("supplierSlug").value="";el("supplierPriority").value="1";el("supplierMetadata").value="{}";el("supplierCredentialType").value="";el("supplierCredentialRef").value="";el("supplierDialog").showModal();
}

async function saveSupplier(event){
  event.preventDefault(); const body={name:el("supplierName").value.trim(),slug:el("supplierSlug").value.trim().toLowerCase(),provider_type:el("supplierType").value,priority:Number(el("supplierPriority").value||1),metadata:parseJson("supplierMetadata")};
  const created=await api("/api/v1/admin/providers",{method:"POST",body:JSON.stringify(body)});
  const ref=el("supplierCredentialRef").value.trim(); const type=el("supplierCredentialType").value.trim();
  if(ref||type){if(!ref||!type) throw new Error("Credential type and secret reference are both required.");await api(`/api/v1/admin/providers/${created.id}/credentials`,{method:"PUT",body:JSON.stringify({credential_type:type,secret_ref:ref})});}
  el("supplierDialog").close(); await loadProviderOps();
}

async function toggleSupplier(providerId){
  const provider=state.suppliers.find(p=>p.id===providerId);if(!provider)return;
  await api(`/api/v1/admin/providers/${providerId}`,{method:"PATCH",body:JSON.stringify({is_enabled:!provider.is_enabled})});await loadProviderOps();
}

async function healthSupplier(providerId){
  const result=await api(`/api/v1/admin/providers/${providerId}/health-check`,{method:"POST"});alert(`Health: ${result.status}${result.balance!==null?`\nBalance: ${money(result.balance,result.balance_currency)}`:""}`);await loadSuppliers();
}

function defaultPaymentSettings(providerName){
  if(providerName==="telegram_stars")return {display_name:"Telegram Stars",topup_enabled:true,topup_min_amount:"10",topup_max_amount:"10000",topup_currencies:["XTR"],topup_whole_units_only:true,checkout_mode:"telegram_invoice",terms_required:true,terms_url:"",terms_version:"current",invoice_title:"Wallet top-up",invoice_description:"Add Stars to your wallet.",price_label:"Wallet credit",transaction_scan_pages:10,chargeback_reconciliation_enabled:true};
  return {display_name:"Gateway",topup_enabled:true,topup_min_amount:"5.00",topup_max_amount:"500.00",topup_currencies:["USD"]};
}

function openPaymentProvider(providerName=null){
  const selected=providerName||state.providerCapabilities?.payment_provider_names?.[0]||"";const existing=state.paymentProviders.find(p=>p.provider_name===selected);el("paymentProviderName").value=selected;el("paymentProviderName").disabled=!!existing;el("paymentProviderEnabled").checked=existing?.is_enabled??true;el("paymentCredentialRef").value="";el("paymentWebhookRef").value="";el("paymentProviderSettings").value=JSON.stringify(existing?.settings||defaultPaymentSettings(selected),null,2);el("paymentProviderDialog").showModal();
}

async function savePaymentProvider(event){
  event.preventDefault();const name=el("paymentProviderName").value;const body={is_enabled:el("paymentProviderEnabled").checked,settings:parseJson("paymentProviderSettings")};const cred=el("paymentCredentialRef").value.trim();const webhook=el("paymentWebhookRef").value.trim();if(cred)body.credentials_ref=cred;if(webhook)body.webhook_secret_ref=webhook;await api(`/api/v1/admin/payment-providers/${encodeURIComponent(name)}`,{method:"PUT",body:JSON.stringify(body)});el("paymentProviderDialog").close();el("paymentProviderName").disabled=false;await loadProviderOps();
}

function openMapping(){
  if(!state.suppliers.length) throw new Error("Create a supplier provider first.");if(!state.products.length) throw new Error("Create a product first.");el("mappingExternalId").value="";el("mappingCost").value="0";el("mappingCurrency").value="USD";el("mappingPriority").value="";refreshMappingVariants();el("mappingDialog").showModal();
}

async function saveMapping(event){
  event.preventDefault();const priority=el("mappingPriority").value;const body={product_id:el("mappingProduct").value,product_variant_id:el("mappingVariant").value||null,external_product_id:el("mappingExternalId").value.trim(),cost_price:el("mappingCost").value||"0",cost_currency:el("mappingCurrency").value.trim().toUpperCase(),priority_override:priority?Number(priority):null,provider_metadata:{}};await api(`/api/v1/admin/providers/${el("mappingProvider").value}/mappings`,{method:"POST",body:JSON.stringify(body)});el("mappingDialog").close();await loadProviderOps();
}

function openProduct(product=null){
  el("productId").value=product?.id||""; el("productTitle").value=product?.title||""; el("productDescription").value=product?.description||""; el("productCategory").value=product?.category_id||""; el("productEnabled").checked=product?.is_active??true;
  el("productDialogTitle").textContent=product?"Edit product":"New product"; el("variantFields").classList.toggle("hidden",!!product);
  if(!product){ el("variantSku").value=""; el("variantTitle").value="Standard"; el("variantPrice").value=""; el("variantCurrency").value="USD"; el("variantStock").value="0"; }
  el("productDialog").showModal();
}

async function saveProduct(event){
  event.preventDefault(); const id=el("productId").value;
  const body={title:el("productTitle").value.trim(),description:el("productDescription").value.trim()||null,category_id:el("productCategory").value||null,is_active:el("productEnabled").checked};
  if(!id){
    const sku=el("variantSku").value.trim();
    body.variants=sku?[{sku,title:el("variantTitle").value.trim()||"Standard",price:el("variantPrice").value,currency:el("variantCurrency").value.trim().toUpperCase(),stock_quantity:Number(el("variantStock").value||0),is_active:true,attributes:{}}]:[];
  }
  await api(id?`/api/v1/admin/catalog/products/${id}`:"/api/v1/admin/catalog/products",{method:id?"PATCH":"POST",body:JSON.stringify(body)});
  el("productDialog").close(); await Promise.all([loadProducts(),loadBootstrap()]);
}

async function refreshCurrent(){ const active=document.querySelector(".nav.active")?.dataset.view; if(active==="bots") await loadBots(); else if(active==="products") await Promise.all([loadCategories(),loadProducts()]); else if(active==="orders") await loadOrders(); else if(active==="fulfillment") await loadFulfillment(); else if(active==="providers") await loadProviderOps(); else if(active==="members") await loadMembers(); else if(active==="finance") await loadFinancialCases(); else if(active==="analytics") await loadAnalytics(); else if(active==="audit") await loadAuditLogs(0); else if(active==="reconciliation") await loadEvents(); else await loadBootstrap(); }

function bind(){
  document.querySelectorAll(".nav").forEach(b=>b.addEventListener("click",async()=>{document.querySelectorAll(".nav,.view").forEach(n=>n.classList.remove("active"));b.classList.add("active");el(`view-${b.dataset.view}`).classList.add("active");el("viewTitle").textContent=b.textContent;await refreshCurrent();}));
  el("refreshButton").addEventListener("click",refreshCurrent); el("newProduct").addEventListener("click",()=>openProduct()); el("productForm").addEventListener("submit",saveProduct);
  el("newBot").addEventListener("click",()=>openBotProvision());el("botProvisionForm").addEventListener("submit",e=>saveBotProvision(e).catch(err=>alert(err.message)));document.querySelectorAll("[data-close-bot]").forEach(b=>b.addEventListener("click",()=>el("botProvisionDialog").close()));el("botWizardNext").addEventListener("click",()=>{if(state.botWizardStep===2&&state.botWizardMode!=="edit"&&!el("botToken").value.trim()){alert("Paste the BotFather token.");return;}setWizardStep(state.botWizardMode==="edit"&&state.botWizardStep===1?3:state.botWizardStep+1);});el("botWizardBack").addEventListener("click",()=>setWizardStep(state.botWizardMode==="edit"&&state.botWizardStep===3?1:state.botWizardStep-1));el("botTemplate").addEventListener("change",()=>applyBotTemplateDefaults(selectedBotTemplate(),{preserveIdentity:true}));["botDisplayName","botStoreTagline","botBrandAccent","botLogoUrl"].forEach(id=>el(id).addEventListener("input",renderBotBrandPreview));
  el("botList").addEventListener("click",e=>{const configure=e.target.closest("[data-configure-bot]")?.dataset.configureBot;const verify=e.target.closest("[data-verify-bot-credential]")?.dataset.verifyBotCredential;const rotate=e.target.closest("[data-rotate-bot-credential]")?.dataset.rotateBotCredential;const launchCheck=e.target.closest("[data-launch-check-bot]")?.dataset.launchCheckBot;const restart=e.target.closest("[data-restart-bot]")?.dataset.restartBot;const releaseNode=e.target.closest("[data-release-channel-bot]");const node=e.target.closest("[data-toggle-bot]");if(configure){const bot=state.bots.find(b=>b.id===configure);if(bot)openBotProvision(bot);}else if(verify)verifyBotCredential(verify).catch(err=>alert(err.message));else if(rotate)openBotCredentialRotation(rotate);else if(launchCheck)openBotLaunchCheck(launchCheck).catch(err=>alert(err.message));else if(restart)restartBotRuntime(restart).catch(err=>alert(err.message));else if(releaseNode)toggleBotReleaseChannel(releaseNode.dataset.releaseChannelBot,releaseNode.dataset.releaseChannel).catch(err=>alert(err.message));else if(node)toggleBot(node.dataset.toggleBot,node.dataset.enabled==="true").catch(err=>alert(err.message));});
  el("botCredentialForm").addEventListener("submit",e=>saveBotCredentialRotation(e).catch(err=>alert(err.message)));document.querySelectorAll("[data-close-bot-credential]").forEach(b=>b.addEventListener("click",()=>el("botCredentialDialog").close()));
  document.querySelectorAll("[data-close-bot-launch]").forEach(b=>b.addEventListener("click",()=>el("botLaunchDialog").close()));
  el("botJobList").addEventListener("click",e=>{const retry=e.target.closest("[data-retry-bot-job]")?.dataset.retryBotJob;const cancel=e.target.closest("[data-cancel-bot-job]")?.dataset.cancelBotJob;if(retry)retryBotJob(retry).catch(err=>alert(err.message));else if(cancel)cancelBotJob(cancel).catch(err=>alert(err.message));});
  document.querySelectorAll("[data-close-dialog]").forEach(b=>b.addEventListener("click",()=>el("productDialog").close()));
  document.querySelectorAll("[data-close-supplier]").forEach(b=>b.addEventListener("click",()=>el("supplierDialog").close()));document.querySelectorAll("[data-close-payment-provider]").forEach(b=>b.addEventListener("click",()=>{el("paymentProviderDialog").close();el("paymentProviderName").disabled=false;}));document.querySelectorAll("[data-close-mapping]").forEach(b=>b.addEventListener("click",()=>el("mappingDialog").close()));document.querySelectorAll("[data-close-member]").forEach(b=>b.addEventListener("click",()=>el("memberDialog").close()));document.querySelectorAll("[data-close-financial]").forEach(b=>b.addEventListener("click",()=>el("financialResolutionDialog").close()));
  let timer; el("productSearch").addEventListener("input",()=>{clearTimeout(timer);timer=setTimeout(loadProducts,250)}); el("productActive").addEventListener("change",loadProducts); el("orderSearch").addEventListener("input",()=>{clearTimeout(timer);timer=setTimeout(loadOrders,250)}); el("orderStatus").addEventListener("change",loadOrders); el("fulfillmentSearch").addEventListener("input",()=>{clearTimeout(timer);timer=setTimeout(loadFulfillment,250)}); el("fulfillmentStatus").addEventListener("change",loadFulfillment); el("memberSearch").addEventListener("input",()=>{clearTimeout(timer);timer=setTimeout(loadMembers,250)}); el("memberRole").addEventListener("change",loadMembers); el("memberActive").addEventListener("change",loadMembers); el("reviewFilter").addEventListener("change",loadEvents);el("financeSearch").addEventListener("input",()=>{clearTimeout(timer);timer=setTimeout(loadFinancialCases,250)});el("financeStatus").addEventListener("change",loadFinancialCases);el("financeSeverity").addEventListener("change",loadFinancialCases);el("financeAssigned").addEventListener("change",loadFinancialCases);el("analyticsDays").addEventListener("change",loadAnalytics);el("auditSearch").addEventListener("input",()=>{clearTimeout(timer);timer=setTimeout(()=>loadAuditLogs(0),250)});el("auditAction").addEventListener("input",()=>{clearTimeout(timer);timer=setTimeout(()=>loadAuditLogs(0),250)});el("auditResourceType").addEventListener("input",()=>{clearTimeout(timer);timer=setTimeout(()=>loadAuditLogs(0),250)});el("auditLoadMore").addEventListener("click",()=>{if(state.auditNextOffset!=null)loadAuditLogs(state.auditNextOffset).catch(err=>alert(err.message));});
  el("productList").addEventListener("click",e=>{const id=e.target.closest("[data-edit-product]")?.dataset.editProduct;if(id) openProduct(state.products.find(p=>p.id===id));});
  el("financialCaseList").addEventListener("click",async e=>{const claim=e.target.closest("[data-claim-finance]")?.dataset.claimFinance;const release=e.target.closest("[data-release-finance]")?.dataset.releaseFinance;const resolve=e.target.closest("[data-resolve-finance]")?.dataset.resolveFinance;try{if(claim)await claimFinancialCase(claim);else if(release)await releaseFinancialCase(release);else if(resolve)openFinancialResolution(resolve);}catch(err){alert(err.message||"Financial operation failed.");}});el("financialResolutionForm").addEventListener("submit",e=>resolveFinancialCase(e).catch(err=>alert(err.message)));
  el("fulfillmentList").addEventListener("click",async e=>{const requeue=e.target.closest("[data-requeue-job]")?.dataset.requeueJob;const reconcile=e.target.closest("[data-reconcile-order]")?.dataset.reconcileOrder;try{if(requeue) await requeueJob(requeue);else if(reconcile) await reconcileOrder(reconcile);}catch(error){alert(error.message||"Fulfillment operation failed.");}});
  el("newSupplier").addEventListener("click",openSupplier);el("supplierForm").addEventListener("submit",e=>saveSupplier(e).catch(err=>alert(err.message)));el("configurePayment").addEventListener("click",()=>openPaymentProvider());el("paymentProviderForm").addEventListener("submit",e=>savePaymentProvider(e).catch(err=>alert(err.message)));el("paymentProviderName").addEventListener("change",e=>{const name=e.target.value;if(!state.paymentProviders.some(p=>p.provider_name===name))el("paymentProviderSettings").value=JSON.stringify(defaultPaymentSettings(name),null,2);});el("newMapping").addEventListener("click",()=>{try{openMapping();}catch(err){alert(err.message);}});el("mappingForm").addEventListener("submit",e=>saveMapping(e).catch(err=>alert(err.message)));el("mappingProduct").addEventListener("change",refreshMappingVariants);
  el("supplierProviderList").addEventListener("click",async e=>{const health=e.target.closest("[data-health-provider]")?.dataset.healthProvider;const toggle=e.target.closest("[data-toggle-provider]")?.dataset.toggleProvider;try{if(health)await healthSupplier(health);else if(toggle)await toggleSupplier(toggle);}catch(err){alert(err.message||"Provider operation failed.");}});el("paymentProviderList").addEventListener("click",e=>{const name=e.target.closest("[data-edit-payment-provider]")?.dataset.editPaymentProvider;if(name)openPaymentProvider(name);});el("memberList").addEventListener("click",e=>{const id=e.target.closest("[data-edit-member]")?.dataset.editMember;if(id){const member=state.members.find(m=>m.id===id);if(member)openMember(member);}});el("memberForm").addEventListener("submit",e=>saveMember(e).catch(err=>alert(err.message)));
}

(async()=>{try{await authenticate();await Promise.all([loadBootstrap(),loadCategories()]);bind();el("app").classList.remove("hidden");}catch(error){el("fatal").textContent=error.message||"Admin console failed to load.";el("fatal").classList.remove("hidden");}})();
