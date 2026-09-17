const tg = window.Telegram?.WebApp;
const state = { token: null, botId: null, bootstrap: null, categories: [], products: [], orders: [], jobs: [], events: [], providerCapabilities: null, providerAdapters: [], suppliers: [], paymentProviders: [], mappings: [], members: [], financialCases: [], paymentOps: null, analytics: null, auditLogs: [], auditNextOffset: null, bots: [], botJobs: [], botTemplates: [], botWizardStep: 1, botWizardMode: "create", botWizardOptions: null, saas: null, billing: null, platformToken: null, inquiries: [], salesQuotes: [], currentInquiry: null, salesActiveTab: "inquiries", adminIntegrations: [] };
const el = (id) => document.getElementById(id);
const escapeHtml = (v) => String(v ?? "").replace(/[&<>'"]/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[c]));

const TOKEN_KEY = "ghbf_admin_token";

function setSessionToken(token) {
  state.token = token;
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch (_) {}
}

function clearSessionToken() {
  setSessionToken(null);
}

function getStoredToken() {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch (_) {
    return null;
  }
}

async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  const response = await fetch(path, { ...options, headers });
  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try { const parsed=(await response.json()).detail; detail=typeof parsed==="string"?parsed:(parsed?.message||JSON.stringify(parsed)||detail); } catch (_) {}
    if (response.status === 401 && !path.includes("/auth/")) {
      clearSessionToken();
      showLogin("Session expired. Send /admin to your bot for a new sign-in link.");
    }
    throw new Error(detail);
  }
  if (response.status === 204) return null;
  return response.json();
}

async function authenticate() {
  const params = new URLSearchParams(location.search);
  state.botId = params.get("bot_id");
  if (!state.botId || !tg?.initData) return false;
  tg.ready(); tg.expand();
  const auth = await api("/api/v1/auth/telegram-miniapp", {
    method: "POST",
    body: JSON.stringify({ init_data: tg.initData, bot_id: state.botId }),
  });
  state.token = auth.access_token;
  return true;
}

let bound = false;
function showLogin(message = "") {
  clearSessionToken();
  el("app").classList.add("hidden");
  el("login").classList.remove("hidden");
  el("loginError").textContent = message;
  el("loginCode").focus();
}

async function openConsole() {
  await Promise.all([loadBootstrap(), loadCategories()]);
  if (!bound) { bind(); bound = true; }
  el("login").classList.add("hidden");
  el("app").classList.remove("hidden");
}

async function signIn(event) {
  event.preventDefault();
  const button = el("loginSubmit");
  button.disabled = true;
  button.textContent = "Signing in…";
  el("loginError").textContent = "";
  try {
    const result = await api("/api/v1/auth/admin-code", {
      method: "POST", body: JSON.stringify({ code: el("loginCode").value.trim() }),
    });
    el("loginCode").value = "";
    setSessionToken(result.access_token);
    await openConsole();
  } catch (error) {
    el("loginCode").value = "";
    showLogin(error.message || "Sign-in failed. Send /admin to your bot for a new code.");
  } finally {
    button.disabled = false;
    button.textContent = "Sign in";
  }
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
  document.querySelectorAll("[data-admin-only]").forEach(node=>node.classList.toggle("hidden",!["ADMIN","OWNER"].includes(state.bootstrap.actor.role)));
  loadOnboardingChecklist().catch(()=>{});
}

async function loadOnboardingChecklist() {
  const card = el("onboardingChecklistCard");
  if (!card) return;
  try {
    const data = await api("/api/v1/admin/onboarding/checklist");
    if (data.launch_ready) {
      card.classList.add("hidden");
      return;
    }
    card.classList.remove("hidden");
    el("onboardingProgressBadge").textContent = `${data.progress_percent}% Complete`;
    el("onboardingProgressBadge").className = `chip ${data.progress_percent >= 60 ? "chip-ok" : "chip-accent"}`;
    el("onboardingProgressBar").style.width = `${data.progress_percent}%`;
    el("onboardingNextStep").innerHTML = `<strong>Next step:</strong> ${escapeHtml(data.next_step)}`;

    el("onboardingItemsList").innerHTML = data.items.map((it) => `
      <div class="item-row" style="background:#0c1322;padding:8px 12px;border-radius:10px;border:1px solid ${it.completed ? "rgba(85,211,159,0.3)" : "var(--line)"}">
        <div style="display:flex;align-items:center;gap:10px">
          <span style="font-size:16px">${it.completed ? "✅" : "⭕"}</span>
          <div>
            <strong style="color:${it.completed ? "var(--text)" : "var(--muted)"}">${escapeHtml(it.title)}</strong>
            <small style="display:block;color:var(--muted)">${escapeHtml(it.description)}</small>
          </div>
        </div>
        <div>
          ${it.completed ? `<span class="chip chip-ok">Ready</span>` : `<button type="button" class="ghost" data-onboarding-target="${escapeHtml(it.action_view)}">Configure →</button>`}
        </div>
      </div>
    `).join("");
  } catch (_) {
    card.classList.add("hidden");
  }
}

function formatMinorUnits(amount,currency){try{const nf=new Intl.NumberFormat(undefined,{style:"currency",currency});const digits=nf.resolvedOptions().maximumFractionDigits;return nf.format(Number(amount)/(10**digits));}catch(_){return `${amount} ${currency} minor units`;}}

function saasFeatureEnabled(key){return state.saas?.source==="SELF_HOSTED_DEFAULTS"||state.saas?.effective_features?.[key]===true;}
function commercialMutationAllowed(){return state.saas?.commercial_access?.allowed!==false;}

async function loadSaas(){
  const [saas,billing]=await Promise.all([api("/api/v1/admin/saas/overview"),api("/api/v1/admin/saas/billing")]);
  state.saas=saas;state.billing=billing;const s=saas;
  el("saasPlanName").textContent=s.plan_name||"Self-hosted";
  el("saasPlanStatus").innerHTML=statusChip(s.subscription_status||(s.source==="SELF_HOSTED_DEFAULTS"?"SELF_HOSTED":"UNASSIGNED"));
  el("saasUsageGrid").innerHTML=[
    ["Bots",s.usage.bots,`${s.limits.max_bots} allowed`],
    ["Enabled bots",s.usage.enabled_bots,`${s.limits.max_enabled_bots} allowed`],
    ["Provisioning jobs",s.usage.open_provisioning_jobs,`${s.limits.max_open_provisioning_jobs} open allowed`],
    ["Entitlement source",s.plan_key||"Local defaults",s.source],
  ].map(([label,value,hint])=>`<article class="metric"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong><small class="muted">${escapeHtml(hint)}</small></article>`).join("");
  const features=Object.entries(s.effective_features||s.features||{});
  el("saasFeatureList").innerHTML=features.length?features.map(([key,enabled])=>`<article class="item compact-item"><div class="item-row"><strong>${escapeHtml(key)}</strong>${statusChip(enabled?"AVAILABLE":"BLOCKED")}</div></article>`).join(""):`<div class="empty">No product feature flags are configured yet.</div>`;
  el("billingProviderStatus").innerHTML=statusChip(billing.provider_configured?(billing.provider||"CONFIGURED").toUpperCase():"LOCAL_ONLY");
  const sub=billing.subscription;const grace=sub?.grace_ends_at?` Past-due grace ends ${new Date(sub.grace_ends_at).toLocaleString()}.`:"";
  const access=s.commercial_access||{state:"UNKNOWN",allowed:true,reason:""};
  el("billingPolicy").textContent=`Commercial access: ${access.state}. ${access.reason||""} Provider state is authoritative; server-side Phase 9.3 gates enforce premium capabilities and growth after grace. Configured past-due grace is ${billing.policy.past_due_grace_days} day(s).${grace}`;
  const portal=el("billingPortal");portal.classList.toggle("hidden",!billing.portal_available||!canOperateBots());
  const catalog=billing.catalog||[];
  el("billingCatalog").innerHTML=catalog.length?catalog.map(p=>`<article class="item compact-item"><div class="item-row"><div><strong>${escapeHtml(p.plan_name)}</strong><div class="item-meta"><span>${escapeHtml(formatMinorUnits(p.unit_amount_minor,p.currency))}</span><span>every ${p.interval_count>1?`${p.interval_count} `:""}${escapeHtml(p.interval.toLowerCase())}${p.interval_count>1?"s":""}</span></div><p class="muted compact">${escapeHtml(p.plan_description||p.plan_key)}</p></div>${billing.checkout_available&&canOperateBots()?`<button class="primary" data-billing-checkout="${p.id}">Choose plan</button>`:""}</div></article>`).join(""):(billing.provider_configured?`<div class="empty">No public billing prices are configured yet.</div>`:`<div class="empty">Hosted billing is disabled. Self-hosted operation remains available.</div>`);
}

function openBillingUrl(url){if(tg?.openLink)tg.openLink(url);else window.location.assign(url);}
async function startBillingCheckout(priceId){const key=crypto.randomUUID?.()||`billing-${Date.now()}-${Math.random()}`;const result=await api("/api/v1/admin/saas/billing/checkout",{method:"POST",headers:{"Idempotency-Key":key},body:JSON.stringify({price_id:priceId})});openBillingUrl(result.url);}
async function openBillingPortal(){const result=await api("/api/v1/admin/saas/billing/portal",{method:"POST"});openBillingUrl(result.url);}

function canOperateBots(){return ["ADMIN","OWNER"].includes(state.bootstrap?.actor?.role);}

async function loadBots(){
  if(!state.saas)await loadSaas();
  const [fleet,jobs,templates]=await Promise.all([
    api("/api/v1/admin/bots/fleet"),
    api("/api/v1/admin/bots/jobs?limit=50"),
    api("/api/v1/admin/bots/templates"),
  ]);
  state.bots=fleet.bots;state.botJobs=jobs.jobs;state.botTemplates=templates.templates;
  el("newBot").disabled=!commercialMutationAllowed();
  const typeBadges = {
    NUMBER_SMS: "📱 Numbers & SMS",
    ACCOUNT: "👤 Accounts",
    GIFT_CARD: "🎁 Gift Cards",
    DIGITAL_PRODUCT: "⚡ Digital",
    RESELLER: "🌐 Reseller",
    HYBRID: "✨ Hybrid",
    GENERAL: "🛍️ General",
  };
  el("botList").innerHTML=state.bots.length?state.bots.map(b=>{
    const bType = b.business_profile?.business_type || "GENERAL";
    const bBadge = typeBadges[bType] || `📦 ${bType}`;
    const pCount = b.business_profile?.provider_ids?.length;
    const provSummary = pCount ? `${pCount} provider${pCount===1?'':'s'}` : "All compatible";
    const rStrategy = b.business_profile?.routing_strategy || "default routing";
    return `<article class="item"><div class="item-row"><div><h3>${escapeHtml(b.display_name)} ${b.username?`<span class="chip">@${escapeHtml(b.username)}</span>`:""} <span class="chip chip-accent">${escapeHtml(bBadge)}</span></h3><div class="item-meta"><span>Telegram ${escapeHtml(b.telegram_bot_id)}</span><span>Credential v${escapeHtml(b.credential_version)} · ${escapeHtml(b.credential_status)}</span><span>Runtime ${escapeHtml(b.runtime_status)}</span><span>Channel ${escapeHtml(b.release_channel)}</span><span>${b.template_key?`${escapeHtml(b.template_key)} v${escapeHtml(b.template_version||1)}`:"legacy config"}</span><span>${escapeHtml(provSummary)}</span><span>${escapeHtml(rStrategy)}</span><span>Updated ${new Date(b.updated_at).toLocaleString()}</span></div>${b.runtime_detail?`<p class="muted compact">Runtime: ${escapeHtml(b.runtime_detail)}</p>`:""}</div><div class="ops-actions">${statusChip(b.desired_state)} ${statusChip(b.runtime_status)} ${statusChip(b.credential_status)}${canOperateBots()?`<button class="ghost" data-verify-bot-credential="${b.id}">Verify credential</button><button class="ghost" data-rotate-bot-credential="${b.id}">Rotate token</button><button class="ghost" data-launch-check-bot="${b.id}">Launch check</button><button class="ghost" data-restart-bot="${b.id}" ${b.is_enabled&&commercialMutationAllowed()&&saasFeatureEnabled("runtime_controls")?"":"disabled"}>Restart runtime</button><button class="ghost" data-release-channel-bot="${b.id}" data-release-channel="${b.release_channel}" ${b.release_channel==="CANARY"||commercialMutationAllowed()&&saasFeatureEnabled("canary_rollout")?"":"disabled"}>${b.release_channel==="CANARY"?"Promote stable":"Move to canary"}</button><button class="ghost" data-configure-bot="${b.id}">Configure</button><button class="ghost" data-toggle-bot="${b.id}" data-enabled="${b.is_enabled}" ${b.is_enabled||commercialMutationAllowed()?"":"disabled"}>${b.is_enabled?"Disable":"Enable"}</button>`:""}</div></div></article>`;
  }).join(""):`<div class="empty">No bots provisioned for this tenant.</div>`;
  el("botJobList").innerHTML=state.botJobs.length?state.botJobs.map(j=>`<article class="item"><div class="item-row"><div><h3>${escapeHtml(j.expected_username?`@${j.expected_username}`:(j.requested_display_name||"Bot provisioning"))}</h3><div class="item-meta"><span>Attempt ${j.attempt_count}/${j.max_attempts}</span><span>${j.verified_username?`Verified @${escapeHtml(j.verified_username)}`:"Identity pending"}</span><span>${new Date(j.created_at).toLocaleString()}</span></div><p class="muted compact">${escapeHtml(j.last_error_code||"Durable job awaiting or completed verification.")}</p></div><div class="ops-actions">${statusChip(j.status)}${canOperateBots()&&j.status==="FAILED"?`<button class="ghost" data-retry-bot-job="${j.id}" ${commercialMutationAllowed()?"":"disabled"}>Retry</button>`:""}${canOperateBots()&&["PENDING","RETRY"].includes(j.status)?`<button class="ghost" data-cancel-bot-job="${j.id}">Cancel</button>`:""}</div></div></article>`).join(""):`<div class="empty">No provisioning jobs yet.</div>`;
}

function selectedBotTemplate(){return state.botTemplates.find(t=>t.key===el("botTemplate").value)||state.botTemplates[0]||null;}
function setWizardStep(step){
  state.botWizardStep=Math.max(1,Math.min(5,step));
  document.querySelectorAll("[data-wizard-step]").forEach(n=>n.classList.toggle("hidden",Number(n.dataset.wizardStep)!==state.botWizardStep));
  document.querySelectorAll("[data-step-dot]").forEach(n=>n.classList.toggle("active",Number(n.dataset.stepDot)===state.botWizardStep));
  el("botWizardBack").classList.toggle("hidden",state.botWizardStep===1);
  const last=state.botWizardStep===5;if(last)renderBotWizardReview();
  el("botWizardNext").classList.toggle("hidden",last);
  el("botWizardSubmit").classList.toggle("hidden",!last);
  el("botWizardSubmit").textContent=state.botWizardMode==="edit"?"Save configuration":"Queue provisioning";
}
async function loadBotWizardOptions({preserveSelections=false,profile=null}={}){
  const template=selectedBotTemplate();if(!template)return;
  const previousProviders=preserveSelections?new Set([...document.querySelectorAll('[data-bot-provider]:checked')].map(n=>n.value)):new Set((profile?.provider_ids||[]).map(String));
  const previousMethods=preserveSelections?new Set([...document.querySelectorAll('[data-bot-payment]:checked')].map(n=>n.value)):new Set((profile?.payment_method_ids||[]).map(String));
  state.botWizardOptions=await api(`/api/v1/admin/bots/wizard/options?template_key=${encodeURIComponent(template.key)}`);
  const o=state.botWizardOptions;
  el("botRoutingStrategy").innerHTML=o.routing_strategies.map(v=>`<option value="${escapeHtml(v)}">${escapeHtml(v.replaceAll('_',' '))}</option>`).join("");
  el("botRoutingStrategy").value=profile?.routing_strategy||o.default_routing_strategy||"PRIORITY";
  el("botPricingTier").innerHTML=`<option value="">Tenant default</option>`+o.pricing_tiers.map(t=>`<option value="${t.id}">${escapeHtml(t.display_name)}${t.is_default?' · default':''}</option>`).join("");
  el("botPricingTier").value=profile?.default_pricing_tier_id||"";
  el("botProviderChoices").innerHTML=o.providers.length?o.providers.map(v=>`<label class="choice-card"><input type="checkbox" data-bot-provider value="${v.id}" ${previousProviders.has(String(v.id))?'checked':''}><span><strong>${escapeHtml(v.name)}</strong><small class="muted">${escapeHtml(v.category)} · ${escapeHtml(v.health_status)}</small></span></label>`).join(""):`<div class="empty">No compatible enabled providers yet. You can provision the bot now and add providers later.</div>`;
  el("botPaymentChoices").innerHTML=o.payment_methods.length?o.payment_methods.map(v=>`<label class="choice-card"><input type="checkbox" data-bot-payment value="${v.id}" ${previousMethods.has(String(v.id))?'checked':''}><span><strong>${escapeHtml(v.display_name)}</strong><small class="muted">${escapeHtml(v.method_type)}${v.provider_name?` · ${escapeHtml(v.provider_name)}`:''}${v.flexible_deposits_enabled?' · flexible':''}</small></span></label>`).join(""):`<div class="empty">No enabled payment methods yet. Wallet checkout remains available for pre-funded balances.</div>`;
  el("botAllowAutoCredit").checked=profile?.allow_flexible_auto_credit??false;
  refreshPreferredProvider(profile?.preferred_provider_id||null);
}
function refreshPreferredProvider(selected=null){
  const checked=[...document.querySelectorAll('[data-bot-provider]:checked')];
  el("botPreferredProvider").innerHTML=`<option value="">Select provider</option>`+checked.map(n=>{const label=n.closest('label')?.querySelector('strong')?.textContent||n.value;return `<option value="${n.value}">${escapeHtml(label)}</option>`}).join("");
  if(selected&&checked.some(n=>n.value===String(selected)))el("botPreferredProvider").value=String(selected);
  el("botPreferredProviderWrap").classList.toggle("hidden",el("botRoutingStrategy").value!=="MANUAL");
}
function currentBotBusinessProfile(){
  const template=selectedBotTemplate();const o=state.botWizardOptions||{};
  return {business_type:o.business_type||template?.business_type||"GENERAL",provider_ids:[...document.querySelectorAll('[data-bot-provider]:checked')].map(n=>n.value),payment_method_ids:[...document.querySelectorAll('[data-bot-payment]:checked')].map(n=>n.value),routing_strategy:el("botRoutingStrategy").value||o.default_routing_strategy||"PRIORITY",preferred_provider_id:el("botRoutingStrategy").value==="MANUAL"?(el("botPreferredProvider").value||null):null,default_pricing_tier_id:el("botPricingTier").value||null,allow_flexible_auto_credit:el("botAllowAutoCredit").checked};
}
function renderBotWizardReview(){
  const t=selectedBotTemplate(),p=currentBotBusinessProfile(),providers=[...document.querySelectorAll('[data-bot-provider]:checked')].map(n=>n.closest('label')?.querySelector('strong')?.textContent||n.value),methods=[...document.querySelectorAll('[data-bot-payment]:checked')].map(n=>n.closest('label')?.querySelector('strong')?.textContent||n.value);
  el("botWizardReview").innerHTML=[['Template',t?.name||'—'],['Business type',p.business_type],['Providers',providers.length?providers.join(', '):'All compatible enabled providers'],['Routing',p.routing_strategy+(p.preferred_provider_id?` · preferred ${el("botPreferredProvider").selectedOptions[0]?.textContent||''}`:'')],['Payments',methods.length?methods.join(', '):'All enabled tenant payment methods'],['Pricing',el("botPricingTier").selectedOptions[0]?.textContent||'Tenant default'],['Flexible auto-credit',p.allow_flexible_auto_credit?'Allowed when method permits':'Disabled for this bot']].map(([a,b])=>`<article class="item"><div class="item-row"><strong>${escapeHtml(a)}</strong><span class="muted">${escapeHtml(b)}</span></div></article>`).join("");
}
function applyBotTemplateDefaults(template,{preserveIdentity=false}={}){
  if(!template)return;const c=template.default_config||{},b=c.branding||{};
  el("botCurrency").value=c.currency||"USD";el("botLocale").value=c.locale||"en";el("botBrandAccent").value=b.brand_accent||"#7c6cff";el("botStoreTagline").value=b.store_tagline||"";el("botWelcomeText").value=b.welcome_text||"";el("botLogoUrl").value=b.brand_logo_url||"";el("botSupportContact").value=b.support_contact||"";el("botSupportUrl").value=b.support_url||"";el("botMenuText").value=b.menu_text||"Open Store";
  const mods=new Set(c.enabled_modules||[]);el("botModuleCatalog").checked=mods.has("catalog");el("botModuleOrders").checked=mods.has("orders");el("botModuleAccount").checked=mods.has("account");
  if(!preserveIdentity&&!el("botDisplayName").value)el("botDisplayName").value=template.name;
  renderBotTemplatePreview();renderBotBrandPreview();
}
let currentGuidanceFilter = "all";
let currentGuidanceQuery = "";

function renderBotTemplatePreview() {
  const t = selectedBotTemplate();
  if (!t) {
    el("botTemplatePreview").innerHTML = "<span>No template available.</span>";
    return;
  }
  const g = t.guidance;
  const sourceBadges = {
    stored: "📦 Stored Inventory",
    provider_api: "⚡ Live Supplier APIs",
    hybrid: "🔀 Hybrid (Stored + APIs)",
  };
  const complexityClasses = {
    Low: "low",
    Medium: "medium",
    High: "high",
  };
  const sourceText = g ? (sourceBadges[g.product_source] || g.product_source) : null;
  const complexityText = g ? `${g.operational_complexity} Complexity` : null;
  const complexityClass = g ? (complexityClasses[g.operational_complexity] || "medium") : "";

  el("botTemplatePreview").innerHTML = `
    <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:6px">
      <strong>${escapeHtml(t.name)} · v${escapeHtml(t.version)}</strong>
      <div class="guidance-badges" style="margin:0">
        ${sourceText ? `<span class="badge-source">${escapeHtml(sourceText)}</span>` : ""}
        ${complexityText ? `<span class="badge-complexity ${complexityClass}">${escapeHtml(complexityText)}</span>` : ""}
      </div>
    </div>
    <span>${escapeHtml(t.description)}</span>
    <span><strong>Recommended for:</strong> ${escapeHtml(t.recommended_for)}</span>
    ${g ? `
      <div class="guidance-detail" style="margin-top:8px;border-top:1px solid var(--line);padding-top:8px">
        <div><strong>What you can sell:</strong> <span>${escapeHtml(g.what_you_can_sell)}</span></div>
        <div><strong>Fulfillment:</strong> <span>${escapeHtml(g.delivery_experience)}</span></div>
      </div>
    ` : ""}
  `;
}

function renderTemplateGuidanceList() {
  const listEl = el("templateGuidanceList");
  if (!listEl) return;
  const selectedKey = selectedBotTemplate()?.key;
  const q = currentGuidanceQuery.trim().toLowerCase();

  const filtered = (state.botTemplates || []).filter((t) => {
    const g = t.guidance || {};
    if (currentGuidanceFilter !== "all" && g.product_source !== currentGuidanceFilter) {
      return false;
    }
    if (!q) return true;
    const searchTarget = [
      t.name,
      t.key,
      t.description,
      t.recommended_for,
      g.what_you_can_sell,
      g.delivery_experience,
      g.example_business,
      g.limitations,
      ...(g.setup_requirements || []),
    ].filter(Boolean).join(" ").toLowerCase();
    return searchTarget.includes(q);
  });

  if (!filtered.length) {
    listEl.innerHTML = `<div class="empty" style="grid-column: 1 / -1">No templates match your search or filter criteria.</div>`;
    return;
  }

  const sourceLabels = {
    stored: "📦 Stored Inventory",
    provider_api: "⚡ Live Supplier APIs",
    hybrid: "🔀 Hybrid Store",
  };
  const complexityClasses = {
    Low: "low",
    Medium: "medium",
    High: "high",
  };

  listEl.innerHTML = filtered.map((t) => {
    const g = t.guidance || {};
    const isCurrent = t.key === selectedKey;
    const sourceLabel = sourceLabels[g.product_source] || g.product_source || "Standard";
    const compClass = complexityClasses[g.operational_complexity] || "medium";
    const reqs = (g.setup_requirements || []).map((r) => `<li>${escapeHtml(r)}</li>`).join("");

    return `
      <article class="guidance-card ${isCurrent ? "current" : ""}">
        <div>
          <div class="guidance-card-head">
            <h3>${escapeHtml(t.name)}</h3>
            <span class="chip">${escapeHtml(t.business_type || "GENERAL")}</span>
          </div>
          <div class="guidance-badges">
            <span class="badge-source">${escapeHtml(sourceLabel)}</span>
            ${g.operational_complexity ? `<span class="badge-complexity ${compClass}">${escapeHtml(g.operational_complexity)} Complexity</span>` : ""}
            <span class="chip">${escapeHtml(t.default_routing_strategy || "PRIORITY")}</span>
          </div>
          <p class="muted" style="margin:0 0 8px;font-size:13px">${escapeHtml(t.description)}</p>

          <div class="guidance-detail">
            <strong>What you can sell</strong>
            <span>${escapeHtml(g.what_you_can_sell || "General store catalog.")}</span>

            <strong>Delivery experience</strong>
            <span>${escapeHtml(g.delivery_experience || "Standard order delivery.")}</span>

            ${reqs ? `<strong>Key setup requirements</strong><ul>${reqs}</ul>` : ""}

            ${g.example_business ? `<strong>Real-world example</strong><span>${escapeHtml(g.example_business)}</span>` : ""}

            ${g.limitations ? `<strong>Considerations &amp; limits</strong><span class="muted">${escapeHtml(g.limitations)}</span>` : ""}
          </div>
        </div>

        <div class="guidance-card-actions">
          <button type="button" class="${isCurrent ? "ghost" : "primary"}" data-select-template="${escapeHtml(t.key)}">
            ${isCurrent ? "✓ Currently Selected" : "Select this template"}
          </button>
        </div>
      </article>
    `;
  }).join("");
}

function openTemplateGuidance() {
  currentGuidanceFilter = "all";
  currentGuidanceQuery = "";
  if (el("templateGuidanceSearch")) el("templateGuidanceSearch").value = "";
  document.querySelectorAll("[data-guidance-filter]").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.guidanceFilter === "all");
  });
  renderTemplateGuidanceList();
  el("templateGuidanceDialog").showModal();
}
function renderBotBrandPreview(){const name=el("botDisplayName").value.trim()||selectedBotTemplate()?.name||"My Store";const tagline=el("botStoreTagline").value.trim()||"Your storefront preview";const accent=el("botBrandAccent").value||"#7c6cff";const logo=el("botLogoUrl").value.trim();const mark=el("botBrandPreviewMark");mark.style.backgroundColor=accent;mark.style.backgroundImage=logo?`url("${logo.replaceAll('"','%22')}")`:"";mark.textContent=logo?"":(name.charAt(0).toUpperCase()||"G");el("botBrandPreviewName").textContent=name;el("botBrandPreviewTagline").textContent=tagline;}
function templateKeyForBot(bot){return bot?.template_key||bot?.config?._factory?.template_key||"general-commerce";}
async function openBotProvision(bot=null){
  state.botWizardMode=bot?"edit":"create";el("botWizardBotId").value=bot?.id||"";el("botWizardTitle").textContent=bot?"Configure bot":"Create a bot";
  el("botTemplate").innerHTML=state.botTemplates.map(t=>`<option value="${escapeHtml(t.key)}">${escapeHtml(t.name)} · v${escapeHtml(t.version)}</option>`).join("");
  const key=templateKeyForBot(bot);if(key&&state.botTemplates.some(t=>t.key===key))el("botTemplate").value=key;
  el("botToken").value="";el("botExpectedUsername").value=bot?.username||"";el("botDisplayName").value=bot?.display_name||"";el("botEnabled").checked=bot?.is_enabled??true;
  el("botCredentialFields").classList.toggle("hidden",Boolean(bot));el("botCredentialPreserved").classList.toggle("hidden",!bot);el("botToken").required=!bot;
  applyBotTemplateDefaults(selectedBotTemplate(),{preserveIdentity:Boolean(bot)});
  if(bot){const c=bot.config||{},b=c.branding||{};el("botCurrency").value=c.currency||el("botCurrency").value;el("botLocale").value=c.locale||el("botLocale").value;el("botBrandAccent").value=b.brand_accent||el("botBrandAccent").value;el("botStoreTagline").value=b.store_tagline||"";el("botWelcomeText").value=b.welcome_text||"";el("botLogoUrl").value=b.brand_logo_url||"";el("botSupportContact").value=b.support_contact||"";el("botSupportUrl").value=b.support_url||"";el("botMenuText").value=b.menu_text||"Open Store";const mods=new Set(c.enabled_modules||[]);el("botModuleCatalog").checked=mods.has("catalog");el("botModuleOrders").checked=mods.has("orders");el("botModuleAccount").checked=mods.has("account");}
  await loadBotWizardOptions({profile:bot?.business_profile||bot?.config?._business||null});
  renderBotBrandPreview();setWizardStep(1);el("botProvisionDialog").showModal();
}
function botWizardPayload(){const template=selectedBotTemplate();if(!template)throw new Error("Select a bot template.");const modules=[["catalog","botModuleCatalog"],["orders","botModuleOrders"],["account","botModuleAccount"]].filter(([,id])=>el(id).checked).map(([name])=>name);if(!modules.length)throw new Error("Enable at least one bot module.");return {display_name:el("botDisplayName").value.trim(),template_key:template.key,template_version:template.version,currency:el("botCurrency").value.trim().toUpperCase(),locale:el("botLocale").value.trim(),branding:{brand_accent:el("botBrandAccent").value,store_tagline:el("botStoreTagline").value.trim(),welcome_text:el("botWelcomeText").value.trim(),brand_logo_url:el("botLogoUrl").value.trim(),support_contact:el("botSupportContact").value.trim(),support_url:el("botSupportUrl").value.trim(),menu_text:el("botMenuText").value.trim(),store_button_text:`🛍️ ${el("botMenuText").value.trim()||"Open Store"}`},enabled_modules:modules,business_profile:currentBotBusinessProfile()};}
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

async function loadPaymentOperationsHealth(){
  const h=await api(`/api/v1/payments/operations/health`); state.paymentOps=h;
  const alertText=h.alerts.length?h.alerts.map(a=>`${escapeHtml(a.code.replaceAll("_"," "))}: ${a.count}`).join(" · "):"No operational payment alerts";
  el("paymentOperationsHealth").innerHTML=`<div class="item-row"><div><strong>Payment operations ${statusChip(h.status)}</strong><div class="item-meta"><span>${h.open_intents} open</span><span>${h.unknown_intents} unknown</span><span>${h.creation_ambiguities} creation ambiguities</span><span>${h.stale_provider_intents} stale</span><span>${h.manual_review_observations} manual reviews</span></div><p class="muted compact">${alertText}</p></div><small class="muted">stale after ${h.stale_after_seconds}s</small></div>`;
}

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
  const [capabilities,adapters]=await Promise.all([api("/api/v1/admin/provider-capabilities"),api("/api/v1/admin/provider-adapters")]);
  state.providerCapabilities=capabilities;state.providerAdapters=adapters;
  el("supplierType").innerHTML=adapters.map(v=>`<option value="${escapeHtml(v.key)}">${escapeHtml(v.display_name)} · ${escapeHtml(v.key)}</option>`).join("");
  el("paymentProviderName").innerHTML=capabilities.payment_provider_names.map(v=>`<option value="${escapeHtml(v)}">${escapeHtml(v)}</option>`).join("");
  refreshSupplierAdapterFields();
}

function refreshSupplierAdapterFields(){
  const adapter=state.providerAdapters.find(v=>v.key===el("supplierType").value);const categories=adapter?.categories||state.providerCapabilities?.supplier_provider_categories||[];
  const prior=el("supplierCategory").value;el("supplierCategory").innerHTML=categories.map(v=>`<option value="${escapeHtml(v)}">${escapeHtml(v.replaceAll("_"," "))}</option>`).join("");if(categories.includes(prior))el("supplierCategory").value=prior;
  const firstCredential=adapter?.credentials?.[0];if(firstCredential&&!el("supplierCredentialType").value)el("supplierCredentialType").value=firstCredential.key;
}

async function loadSuppliers(){
  state.suppliers=await api("/api/v1/admin/providers");
  el("supplierProviderList").innerHTML=state.suppliers.length?state.suppliers.map(p=>`<article class="item"><div class="item-row"><div><h3>${escapeHtml(p.name)}</h3><div class="item-meta"><span>${escapeHtml(p.category)}</span><span>${escapeHtml(p.provider_type)}</span><span>Priority ${p.priority}</span><span>${p.mapping_count} mapping${p.mapping_count===1?"":"s"}</span><span>${p.credentials.length?`${p.credentials.length} credential${p.credentials.length===1?"":"s"}`:"No credentials"}</span>${p.missing_required_credentials.length?`<span class="danger">Missing ${escapeHtml(p.missing_required_credentials.join(", "))}</span>`:""}</div><p class="muted compact">${escapeHtml(p.capabilities.join(" · "))}${p.last_health_message?` · ${escapeHtml(p.last_health_message)}`:""}</p></div><div class="ops-actions">${statusChip(p.health_status)} ${statusChip(p.is_enabled?"ENABLED":"DISABLED")}${canConfigureProviders()?`<button class="ghost" data-health-provider="${p.id}">Test connection</button><button class="ghost" data-toggle-provider="${p.id}">${p.is_enabled?"Disable":"Enable"}</button>`:""}</div></div></article>`).join(""):`<div class="empty">No fulfillment providers configured.</div>`;
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
  const locked=!canConfigureProviders(); ["newSupplier","configurePayment","newMapping","openIntegrationsMarketplaceBtn"].forEach(id=>el(id)?.classList.toggle("hidden",locked));
  el("mappingProduct").innerHTML=state.products.map(p=>`<option value="${p.id}">${escapeHtml(p.title)}</option>`).join("");
  refreshMappingVariants();
}

async function loadAdminIntegrations() {
  const container = el("marketplaceIntegrationsList");
  if (!container) return;
  try {
    const items = await api("/api/v1/admin/integrations");
    state.adminIntegrations = items || [];
    renderAdminIntegrations();
  } catch (err) {
    container.innerHTML = `<div class="status-msg error">${escapeHtml(err.message)}</div>`;
  }
}

function renderAdminIntegrations() {
  const container = el("marketplaceIntegrationsList");
  if (!container) return;
  if (!state.adminIntegrations.length) {
    container.innerHTML = `<div class="empty">No marketplace integrations available.</div>`;
    return;
  }

  const statusBadges = {
    CONFIGURED: `<span class="chip chip-ok">✓ Active &amp; Configured</span>`,
    ENTITLED: `<span class="chip chip-accent">🔓 Entitled (Setup required)</span>`,
    LOCKED: `<span class="chip chip-muted">🔒 Upgrade Required</span>`,
  };

  container.innerHTML = state.adminIntegrations
    .map((it) => {
      const badge = statusBadges[it.status] || `<span class="chip">${escapeHtml(it.status)}</span>`;
      const isEntitled = it.is_entitled || it.status === "CONFIGURED";
      const isConfigured = it.is_configured || it.status === "CONFIGURED";

      return `
        <article class="guidance-card ${isConfigured ? "current" : ""}">
          <div>
            <div class="guidance-card-head">
              <h3>${escapeHtml(it.name)}</h3>
              <span class="chip">${escapeHtml(it.category)}</span>
            </div>
            <div class="guidance-badges">
              ${badge}
              <span class="price-tag">+$${escapeHtml(it.setup_fee)} setup · +$${escapeHtml(it.monthly_fee)}/mo</span>
            </div>
            <p class="muted" style="margin:0 0 10px;font-size:13px">${escapeHtml(it.description)}</p>
            <div class="guidance-detail">
              <strong>Supported Features</strong>
              <ul>${it.features.map((f) => `<li>${escapeHtml(f)}</li>`).join("")}</ul>
              <strong>Requirements</strong>
              <ul>${it.requirements.map((r) => `<li>${escapeHtml(r)}</li>`).join("")}</ul>
            </div>
          </div>
          <div class="guidance-card-actions">
            ${
              isEntitled
                ? `<button type="button" class="${isConfigured ? "ghost" : "primary"}" data-configure-integration="${escapeHtml(it.key)}">
                    ${isConfigured ? "Update API Key / Settings" : "⚡ Connect Integration"}
                   </button>`
                : `<button type="button" class="ghost" disabled title="Contact the platform owner to unlock this integration for your plan.">
                    🔒 Locked (Contact Owner)
                   </button>`
            }
          </div>
        </article>
      `;
    })
    .join("");
}

function openConfigureIntegrationModal(key) {
  const item = state.adminIntegrations.find((x) => x.key === key);
  if (!item) return;
  el("configureMarketplaceKey").value = item.key;
  el("configureMarketplaceTitle").textContent = `Connect ${item.name}`;
  el("configureMarketplaceDesc").textContent = `${item.description} Your API credentials will be stored in encrypted SecretStorage.`;
  el("configureMarketplaceApiKey").value = "";
  el("configureMarketplaceDisplayName").value = item.name;
  el("configureMarketplaceIntegrationDialog").showModal();
}

async function saveConfigureIntegration(event) {
  event.preventDefault();
  const key = el("configureMarketplaceKey").value;
  const apiKey = el("configureMarketplaceApiKey").value.trim();
  const displayName = el("configureMarketplaceDisplayName").value.trim();

  try {
    const result = await api(`/api/v1/admin/integrations/${encodeURIComponent(key)}/configure`, {
      method: "POST",
      body: JSON.stringify({
        api_key: apiKey || null,
        display_name: displayName || null,
      }),
    });
    alert(`Success: ${result.message}`);
    el("configureMarketplaceIntegrationDialog").close();
    await Promise.all([loadAdminIntegrations(), loadProviderOps()]);
  } catch (err) {
    alert(err.message);
  }
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
  el("supplierName").value="";el("supplierSlug").value="";el("supplierPriority").value="1";el("supplierMetadata").value="{}";el("supplierCredentialType").value="";el("supplierCredentialValue").value="";el("supplierCredentialRef").value="";refreshSupplierAdapterFields();el("supplierDialog").showModal();
}

async function saveSupplier(event){
  event.preventDefault(); const body={name:el("supplierName").value.trim(),slug:el("supplierSlug").value.trim().toLowerCase(),provider_type:el("supplierType").value,category:el("supplierCategory").value,priority:Number(el("supplierPriority").value||1),metadata:parseJson("supplierMetadata")};
  const created=await api("/api/v1/admin/providers",{method:"POST",body:JSON.stringify(body)});
  const ref=el("supplierCredentialRef").value.trim();const value=el("supplierCredentialValue").value;const type=el("supplierCredentialType").value.trim();
  if(ref||value||type){if(!type||(!ref&&!value)||Boolean(ref)===Boolean(value))throw new Error("Choose a credential type and exactly one of encrypted value or environment secret ref.");const credential={credential_type:type};if(value)credential.secret_value=value;else credential.secret_ref=ref;await api(`/api/v1/admin/providers/${created.id}/credentials`,{method:"PUT",body:JSON.stringify(credential)});}
  el("supplierCredentialValue").value="";el("supplierDialog").close(); await loadProviderOps();
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
  if(providerName==="nowpayments")return {display_name:"NOWPayments",topup_enabled:true,topup_min_amount:"5.00",topup_max_amount:"500.00",topup_currencies:["USD"],pay_currencies:["usdttrc20"],default_pay_currency:"usdttrc20",checkout_mode:"direct_crypto",timeout_seconds:15};
  if(providerName==="triplea")return {display_name:"Triple-A",topup_enabled:true,topup_min_amount:"5.00",topup_max_amount:"500.00",topup_currencies:["USD"],checkout_mode:"hosted_redirect",sandbox:true,timeout_seconds:15};
  if(providerName==="gozapay")return {display_name:"GoZaPay (Experimental)",topup_enabled:true,topup_min_amount:"5.00",topup_max_amount:"500.00",topup_currencies:["USD"],checkout_mode:"hosted_redirect",chain:"tron",coin:"USDT",timeout_seconds:15,webhook_tolerance_seconds:300,nominal_stablecoin_parity_acknowledged:false,experimental_risk_acknowledged:false};
  return {display_name:"Gateway",topup_enabled:true,topup_min_amount:"5.00",topup_max_amount:"500.00",topup_currencies:["USD"]};
}

function openPaymentProvider(providerName=null){
  const selected=providerName||state.providerCapabilities?.payment_provider_names?.[0]||"";const existing=state.paymentProviders.find(p=>p.provider_name===selected);el("paymentProviderName").value=selected;el("paymentProviderName").disabled=!!existing;el("paymentProviderEnabled").checked=existing?.is_enabled??true;el("paymentCredentialValue").value="";el("paymentCredentialRef").value="";el("paymentWebhookValue").value="";el("paymentWebhookRef").value="";el("paymentProviderSettings").value=JSON.stringify(existing?.settings||defaultPaymentSettings(selected),null,2);el("paymentProviderDialog").showModal();
}

async function savePaymentProvider(event){
  event.preventDefault();const name=el("paymentProviderName").value;const body={is_enabled:el("paymentProviderEnabled").checked,settings:parseJson("paymentProviderSettings")};const credValue=el("paymentCredentialValue").value;const credRef=el("paymentCredentialRef").value.trim();const webhookValue=el("paymentWebhookValue").value;const webhookRef=el("paymentWebhookRef").value.trim();if(credValue&&credRef)throw new Error("Choose either an encrypted credential value or a credential secret ref, not both.");if(webhookValue&&webhookRef)throw new Error("Choose either an encrypted webhook secret or a webhook secret ref, not both.");if(credValue)body.credentials_value=credValue;else if(credRef)body.credentials_ref=credRef;if(webhookValue)body.webhook_secret_value=webhookValue;else if(webhookRef)body.webhook_secret_ref=webhookRef;await api(`/api/v1/admin/payment-providers/${encodeURIComponent(name)}`,{method:"PUT",body:JSON.stringify(body)});el("paymentProviderDialog").close();el("paymentProviderName").disabled=false;await loadProviderOps();
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

async function platformApi(path, options = {}) {
  if (!state.platformToken) {
    el("salesOperatorGate")?.classList.remove("hidden");
    throw new Error("Platform operator token required to access sales console.");
  }
  const headers = {
    "Content-Type": "application/json",
    "X-GHBF-Platform-Token": state.platformToken,
    ...(options.headers || {}),
  };
  const res = await fetch(path, { ...options, headers });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    if (res.status === 401 || res.status === 403) {
      state.platformToken = null;
      el("salesOperatorGate")?.classList.remove("hidden");
    }
    throw new Error(err.detail || "Platform request failed.");
  }
  return res.json();
}

async function loadSales() {
  if (!state.platformToken) {
    el("salesOperatorGate")?.classList.remove("hidden");
    if (el("salesInquiryList")) el("salesInquiryList").innerHTML = "";
    if (el("salesQuotesList")) el("salesQuotesList").innerHTML = "";
    return;
  }
  el("salesOperatorGate")?.classList.add("hidden");

  const tab = state.salesActiveTab || "inquiries";
  const status = el("salesStatusFilter")?.value || "";
  const q = el("salesSearch")?.value.trim() || "";

  if (tab === "inquiries") {
    el("salesInquiryList")?.classList.remove("hidden");
    el("salesQuotesList")?.classList.add("hidden");
    const params = new URLSearchParams();
    if (status) params.set("status", status);
    if (q) params.set("search", q);

    try {
      const data = await platformApi(`/api/v1/platform/sales/inquiries?${params}`);
      state.inquiries = data.items || [];
      renderSalesInquiries();
    } catch (err) {
      if (el("salesInquiryList")) el("salesInquiryList").innerHTML = `<div class="status-msg error">${escapeHtml(err.message)}</div>`;
    }
  } else {
    el("salesInquiryList")?.classList.add("hidden");
    el("salesQuotesList")?.classList.remove("hidden");
    const params = new URLSearchParams();
    if (status) params.set("status", status);
    if (q) params.set("search", q);

    try {
      const data = await platformApi(`/api/v1/platform/sales/quotes?${params}`);
      state.salesQuotes = data.items || [];
      renderSalesQuotes();
    } catch (err) {
      if (el("salesQuotesList")) el("salesQuotesList").innerHTML = `<div class="status-msg error">${escapeHtml(err.message)}</div>`;
    }
  }
}

function renderSalesInquiries() {
  const container = el("salesInquiryList");
  if (!container) return;
  if (!state.inquiries.length) {
    container.innerHTML = `<div class="empty">No customer inquiries found.</div>`;
    return;
  }

  const statusBadges = {
    NEW: "chip-accent",
    CONTACTED: "chip",
    QUOTED: "chip-warn",
    CONVERTED: "chip-ok",
    ARCHIVED: "chip-muted",
  };

  container.innerHTML = state.inquiries
    .map((inq) => {
      const badgeClass = statusBadges[inq.status] || "chip";
      return `
        <article class="item">
          <div class="item-row">
            <div>
              <h3>
                ${escapeHtml(inq.contact_handle)}
                <span class="chip">${escapeHtml(inq.contact_method)}</span>
                <span class="chip ${badgeClass}">${escapeHtml(inq.status)}</span>
              </h3>
              <div class="item-meta">
                <span>Inquiry #${inq.id.slice(0, 8)}</span>
                <span>Format: ${escapeHtml(inq.format || "—")}</span>
                <span>Template: ${escapeHtml(inq.template_key || "—")}</span>
                <span>Source: ${escapeHtml(inq.product_source || "—")}</span>
                <span>Hosting: ${escapeHtml(inq.delivery_model || "—")}</span>
                <span>${new Date(inq.created_at).toLocaleString()}</span>
              </div>
              <p class="muted compact">
                <strong>Estimate:</strong> $${escapeHtml(inq.total_one_time || "0.00")} setup · $${escapeHtml(inq.total_monthly || "0.00")}/mo
                ${inq.project_notes ? ` — <em>"${escapeHtml(inq.project_notes)}"</em>` : ""}
              </p>
            </div>
            <div class="ops-actions">
              <button type="button" class="primary" data-inspect-inquiry="${inq.id}">Inspect &amp; Quote →</button>
            </div>
          </div>
        </article>
      `;
    })
    .join("");
}

function renderSalesQuotes() {
  const container = el("salesQuotesList");
  if (!container) return;
  if (!state.salesQuotes.length) {
    container.innerHTML = `<div class="empty">No commercial quotes created yet.</div>`;
    return;
  }

  const quoteBadges = {
    DRAFT: "chip",
    SENT: "chip-accent",
    ACCEPTED: "chip-ok",
    REJECTED: "chip-danger",
    EXPIRED: "chip-muted",
    SUPERSEDED: "chip-muted",
  };

  container.innerHTML = state.salesQuotes
    .map((q) => {
      const bClass = quoteBadges[q.status] || "chip";
      const isAccepted = q.status === "ACCEPTED";
      return `
        <article class="item">
          <div class="item-row">
            <div>
              <h3>
                ${escapeHtml(q.quote_number)} · v${q.version}
                <span class="chip ${bClass}">${escapeHtml(q.status)}</span>
                <span class="chip">${escapeHtml(q.customer_name)}</span>
              </h3>
              <div class="item-meta">
                <span>Contact: ${escapeHtml(q.customer_contact)}</span>
                <span>One-time: $${escapeHtml(q.total_one_time)}</span>
                <span>Monthly: $${escapeHtml(q.total_monthly)}</span>
                <span>Created: ${new Date(q.created_at).toLocaleString()}</span>
                ${q.accepted_at ? `<span>Accepted: ${new Date(q.accepted_at).toLocaleString()}</span>` : ""}
              </div>
              <p class="muted compact">${escapeHtml(q.notes || q.terms || "Standard commercial quote.")}</p>
            </div>
            <div class="ops-actions">
              ${!isAccepted && q.status !== "SUPERSEDED" ? `<button type="button" class="primary" data-accept-quote="${q.id}">Accept &amp; Lock Quote</button>` : ""}
              ${isAccepted && !q.tenant_id ? `<button type="button" class="primary" data-onboard-quote="${q.id}">🚀 Onboard Tenant</button>` : ""}
              ${q.tenant_id ? `<span class="chip chip-ok">✓ Onboarded</span>` : (isAccepted ? `<span class="chip ${bClass}">Locked</span>` : "")}
            </div>
          </div>
        </article>
      `;
    })
    .join("");
}

async function openInquiryDetail(id) {
  try {
    const inq = await platformApi(`/api/v1/platform/sales/inquiries/${id}`);
    state.currentInquiry = inq;
    el("inquiryDetailId").value = inq.id;
    el("inquiryDetailTitle").textContent = `Inquiry #${inq.id.slice(0, 8)} — ${inq.contact_handle}`;
    el("inquiryUpdateStatus").value = inq.status;

    const conf = inq.configuration || {};
    const est = inq.estimated_quote || {};
    const items = (est.items || [])
      .map((it) => `<li>${escapeHtml(it.name)} (${escapeHtml(it.item_type)}): $${escapeHtml(it.amount)}</li>`)
      .join("");

    el("inquiryDetailContent").innerHTML = `
      <article class="item">
        <div class="item-row">
          <div>
            <strong>Contact Information</strong>
            <div class="item-meta">
              <span>Method: ${escapeHtml(inq.contact_method)}</span>
              <span>Handle: ${escapeHtml(inq.contact_handle)}</span>
              <span>Client IP Hash: ${escapeHtml(inq.ip_hash || "—")}</span>
              <span>Created: ${new Date(inq.created_at).toLocaleString()}</span>
            </div>
          </div>
        </div>
      </article>

      <article class="item">
        <div class="item-row">
          <div>
            <strong>Requested Configuration</strong>
            <div class="item-meta">
              <span>Format: ${escapeHtml(conf.format || "—")}</span>
              <span>Template: ${escapeHtml(conf.template_key || "—")}</span>
              <span>Source: ${escapeHtml(conf.product_source || "—")}</span>
              <span>Hosting: ${escapeHtml(conf.delivery_model || "—")}</span>
            </div>
            ${conf.integration_keys?.length ? `<p class="muted compact"><strong>Integrations:</strong> ${escapeHtml(conf.integration_keys.join(", "))}</p>` : ""}
            ${inq.project_notes ? `<p class="muted compact"><strong>Project Notes:</strong> ${escapeHtml(inq.project_notes)}</p>` : ""}
          </div>
        </div>
      </article>

      <article class="item">
        <div class="item-row">
          <div>
            <strong>Estimated Quote Breakdown</strong>
            <div class="item-meta">
              <span>Total Setup: $${escapeHtml(est.total_one_time || "0.00")}</span>
              <span>Total Monthly: $${escapeHtml(est.total_monthly || "0.00")}</span>
            </div>
            ${items ? `<ul style="margin:6px 0 0 16px;font-size:12px;color:var(--muted)">${items}</ul>` : ""}
          </div>
        </div>
      </article>
    `;

    el("inquiryDetailDialog").showModal();
  } catch (err) {
    alert(err.message);
  }
}

function openCreateQuoteDialog(inquiry) {
  if (!inquiry) return;
  el("quoteInquiryId").value = inquiry.id;
  el("quoteCustomerName").value = inquiry.contact_handle.replace(/^@/, "") || "Merchant";
  el("quoteCustomerContact").value = inquiry.contact_handle;
  el("quoteValidDays").value = 30;
  el("quoteCurrency").value = "USD";
  el("quoteTerms").value = "Standard managed hosting agreement with 99.9% uptime SLA.";
  el("quoteNotes").value = inquiry.project_notes || "";

  const container = el("quoteLineItemsContainer");
  const estItems = inquiry.estimated_quote?.items || [
    { name: "Telegram Bot Setup", category: "product_format", item_type: "one_time", amount: "89.00", description: "Base setup" },
    { name: "Monthly Cloud Hosting", category: "product_format", item_type: "recurring", amount: "49.00", description: "Hosting" },
  ];

  container.innerHTML = estItems
    .map(
      (item) => `
      <div class="grid2 quote-line-row" style="background:#0c1322;padding:8px;border-radius:8px;border:1px solid var(--line);margin-bottom:6px">
        <div>
          <input type="text" class="quote-line-name" value="${escapeHtml(item.name)}" placeholder="Item name" required>
          <input type="text" class="quote-line-desc" value="${escapeHtml(item.description || "")}" placeholder="Description" style="margin-top:4px;font-size:12px">
        </div>
        <div style="display:flex;gap:6px">
          <select class="quote-line-type">
            <option value="one_time" ${item.item_type === "one_time" ? "selected" : ""}>One-time</option>
            <option value="recurring" ${item.item_type === "recurring" ? "selected" : ""}>Monthly</option>
          </select>
          <input type="number" step="0.01" min="0" class="quote-line-amount" value="${escapeHtml(item.amount)}" style="width:100px" required>
          <button type="button" class="icon remove-quote-line" title="Remove line">×</button>
        </div>
      </div>
    `
    )
    .join("");

  el("createQuoteDialog").showModal();
}

async function saveCreateQuote(event) {
  event.preventDefault();
  const inquiryId = el("quoteInquiryId").value;
  const rows = document.querySelectorAll("#quoteLineItemsContainer .quote-line-row");
  const lines = [];

  rows.forEach((r) => {
    const name = r.querySelector(".quote-line-name").value.trim();
    const desc = r.querySelector(".quote-line-desc").value.trim() || null;
    const item_type = r.querySelector(".quote-line-type").value;
    const amount = r.querySelector(".quote-line-amount").value.trim();
    if (name && amount) {
      lines.push({ name, description: desc, item_type, amount, category: "general" });
    }
  });

  if (!lines.length) {
    alert("Add at least one line item to the quote.");
    return;
  }

  const payload = {
    customer_name: el("quoteCustomerName").value.trim(),
    customer_contact: el("quoteCustomerContact").value.trim(),
    currency: el("quoteCurrency").value.trim().toUpperCase() || "USD",
    terms: el("quoteTerms").value.trim() || null,
    notes: el("quoteNotes").value.trim() || null,
    valid_days: Number(el("quoteValidDays").value || 30),
    lines,
  };

  try {
    const result = await platformApi(`/api/v1/platform/sales/inquiries/${inquiryId}/quotes`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
    alert(`Quote ${result.quote_number} v${result.version} created successfully! Total: $${result.total_one_time} setup, $${result.total_monthly}/mo`);
    el("createQuoteDialog").close();
    el("inquiryDetailDialog").close();
    await loadSales();
  } catch (err) {
    alert(err.message);
  }
}

async function acceptQuote(quoteId) {
  if (!confirm("Are you sure you want to accept and lock this quote? Preceding drafts will be superseded and the inquiry will be marked CONVERTED.")) return;
  try {
    const result = await platformApi(`/api/v1/platform/sales/quotes/${quoteId}/accept`, {
      method: "POST",
    });
    alert(`Quote ${result.quote_number} accepted and frozen immutably!`);
    await loadSales();
  } catch (err) {
    alert(err.message);
  }
}

function openOnboardTenantDialog(quoteId) {
  const quote = state.salesQuotes.find((q) => q.id === quoteId);
  if (!quote) return;
  el("onboardQuoteId").value = quote.id;
  el("onboardTenantTitle").textContent = `Onboard Tenant for ${quote.customer_name}`;
  el("onboardTenantName").value = quote.customer_name;
  el("onboardTenantSlug").value = quote.customer_name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
  el("onboardOwnerUsername").value = quote.customer_contact.replace(/^@/, "");
  el("onboardTenantDialog").showModal();
}

async function saveOnboardTenant(event) {
  event.preventDefault();
  const quoteId = el("onboardQuoteId").value;
  const payload = {
    tenant_name: el("onboardTenantName").value.trim(),
    tenant_slug: el("onboardTenantSlug").value.trim(),
    owner_username: el("onboardOwnerUsername").value.trim(),
  };

  try {
    const res = await platformApi(`/api/v1/platform/sales/quotes/${quoteId}/onboard`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
    alert(
      `Tenant '${res.tenant_name}' provisioned successfully!\n\n` +
      `• Slug: ${res.tenant_slug}\n` +
      `• Owner: @${res.owner_username}\n` +
      `• Admin Launch URL: ${res.admin_launch_url}\n\n` +
      `The owner can use this launch URL or send /admin in Telegram to sign in.`
    );
    el("onboardTenantDialog").close();
    await loadSales();
  } catch (err) {
    alert(err.message);
  }
}

async function refreshCurrent(){ const active=document.querySelector(".nav.active")?.dataset.view; if(active==="plan") await loadSaas(); else if(active==="bots") await loadBots(); else if(active==="products") await Promise.all([loadCategories(),loadProducts()]); else if(active==="orders") await loadOrders(); else if(active==="fulfillment") await loadFulfillment(); else if(active==="providers") await loadProviderOps(); else if(active==="members") await loadMembers(); else if(active==="finance") await Promise.all([loadFinancialCases(),loadPaymentOperationsHealth()]); else if(active==="analytics") await loadAnalytics(); else if(active==="audit") await loadAuditLogs(0); else if(active==="reconciliation") await loadEvents(); else if(active==="sales") await loadSales(); else await loadBootstrap(); }

function bind(){
  document.querySelectorAll(".nav").forEach(b=>b.addEventListener("click",async()=>{document.querySelectorAll(".nav,.view").forEach(n=>n.classList.remove("active"));b.classList.add("active");el(`view-${b.dataset.view}`).classList.add("active");el("viewTitle").textContent=b.textContent;await refreshCurrent();}));
  el("refreshButton").addEventListener("click",refreshCurrent); el("newProduct").addEventListener("click",()=>openProduct()); el("productForm").addEventListener("submit",saveProduct);
  el("billingPortal").addEventListener("click",()=>openBillingPortal().catch(err=>alert(err.message)));el("billingCatalog").addEventListener("click",e=>{const price=e.target.closest("[data-billing-checkout]")?.dataset.billingCheckout;if(price)startBillingCheckout(price).catch(err=>alert(err.message));});
  el("newBot").addEventListener("click",()=>openBotProvision().catch(err=>alert(err.message)));el("botProvisionForm").addEventListener("submit",e=>saveBotProvision(e).catch(err=>alert(err.message)));document.querySelectorAll("[data-close-bot]").forEach(b=>b.addEventListener("click",()=>el("botProvisionDialog").close()));el("botWizardNext").addEventListener("click",()=>{if(state.botWizardStep===2&&state.botWizardMode!=="edit"&&!el("botToken").value.trim()){alert("Paste the BotFather token.");return;}if(state.botWizardStep===4&&el("botRoutingStrategy").value==="MANUAL"&&!el("botPreferredProvider").value){alert("Manual routing requires a preferred provider.");return;}setWizardStep(state.botWizardStep+1);});el("botWizardBack").addEventListener("click",()=>setWizardStep(state.botWizardStep-1));el("botTemplate").addEventListener("change",()=>{applyBotTemplateDefaults(selectedBotTemplate(),{preserveIdentity:true});loadBotWizardOptions().catch(err=>alert(err.message));});el("botRoutingStrategy").addEventListener("change",()=>refreshPreferredProvider());el("botProviderChoices").addEventListener("change",()=>refreshPreferredProvider(el("botPreferredProvider").value));["botDisplayName","botStoreTagline","botBrandAccent","botLogoUrl"].forEach(id=>el(id).addEventListener("input",renderBotBrandPreview));
  el("openTemplateGuidance")?.addEventListener("click", openTemplateGuidance);
  document.querySelectorAll("[data-close-template-guidance]").forEach((b) => b.addEventListener("click", () => el("templateGuidanceDialog").close()));
  el("templateGuidanceSearch")?.addEventListener("input", (e) => {
    currentGuidanceQuery = e.target.value;
    renderTemplateGuidanceList();
  });
  document.querySelectorAll("[data-guidance-filter]").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("[data-guidance-filter]").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      currentGuidanceFilter = btn.dataset.guidanceFilter;
      renderTemplateGuidanceList();
    });
  });
  el("templateGuidanceList")?.addEventListener("click", (e) => {
    const key = e.target.closest("[data-select-template]")?.dataset.selectTemplate;
    if (key) {
      el("botTemplate").value = key;
      applyBotTemplateDefaults(selectedBotTemplate(), { preserveIdentity: true });
      loadBotWizardOptions().catch((err) => alert(err.message));
      el("templateGuidanceDialog").close();
    }
  });
  el("botList").addEventListener("click",e=>{const configure=e.target.closest("[data-configure-bot]")?.dataset.configureBot;const verify=e.target.closest("[data-verify-bot-credential]")?.dataset.verifyBotCredential;const rotate=e.target.closest("[data-rotate-bot-credential]")?.dataset.rotateBotCredential;const launchCheck=e.target.closest("[data-launch-check-bot]")?.dataset.launchCheckBot;const restart=e.target.closest("[data-restart-bot]")?.dataset.restartBot;const releaseNode=e.target.closest("[data-release-channel-bot]");const node=e.target.closest("[data-toggle-bot]");if(configure){const bot=state.bots.find(b=>b.id===configure);if(bot)openBotProvision(bot).catch(err=>alert(err.message));}else if(verify)verifyBotCredential(verify).catch(err=>alert(err.message));else if(rotate)openBotCredentialRotation(rotate);else if(launchCheck)openBotLaunchCheck(launchCheck).catch(err=>alert(err.message));else if(restart)restartBotRuntime(restart).catch(err=>alert(err.message));else if(releaseNode)toggleBotReleaseChannel(releaseNode.dataset.releaseChannelBot,releaseNode.dataset.releaseChannel).catch(err=>alert(err.message));else if(node)toggleBot(node.dataset.toggleBot,node.dataset.enabled==="true").catch(err=>alert(err.message));});
  el("botCredentialForm").addEventListener("submit",e=>saveBotCredentialRotation(e).catch(err=>alert(err.message)));document.querySelectorAll("[data-close-bot-credential]").forEach(b=>b.addEventListener("click",()=>el("botCredentialDialog").close()));
  document.querySelectorAll("[data-close-bot-launch]").forEach(b=>b.addEventListener("click",()=>el("botLaunchDialog").close()));
  el("botJobList").addEventListener("click",e=>{const retry=e.target.closest("[data-retry-bot-job]")?.dataset.retryBotJob;const cancel=e.target.closest("[data-cancel-bot-job]")?.dataset.cancelBotJob;if(retry)retryBotJob(retry).catch(err=>alert(err.message));else if(cancel)cancelBotJob(cancel).catch(err=>alert(err.message));});
  document.querySelectorAll("[data-close-dialog]").forEach(b=>b.addEventListener("click",()=>el("productDialog").close()));
  document.querySelectorAll("[data-close-supplier]").forEach(b=>b.addEventListener("click",()=>el("supplierDialog").close()));document.querySelectorAll("[data-close-payment-provider]").forEach(b=>b.addEventListener("click",()=>{el("paymentProviderDialog").close();el("paymentProviderName").disabled=false;}));document.querySelectorAll("[data-close-mapping]").forEach(b=>b.addEventListener("click",()=>el("mappingDialog").close()));document.querySelectorAll("[data-close-member]").forEach(b=>b.addEventListener("click",()=>el("memberDialog").close()));document.querySelectorAll("[data-close-financial]").forEach(b=>b.addEventListener("click",()=>el("financialResolutionDialog").close()));
  let timer; el("productSearch").addEventListener("input",()=>{clearTimeout(timer);timer=setTimeout(loadProducts,250)}); el("productActive").addEventListener("change",loadProducts); el("orderSearch").addEventListener("input",()=>{clearTimeout(timer);timer=setTimeout(loadOrders,250)}); el("orderStatus").addEventListener("change",loadOrders); el("fulfillmentSearch").addEventListener("input",()=>{clearTimeout(timer);timer=setTimeout(loadFulfillment,250)}); el("fulfillmentStatus").addEventListener("change",loadFulfillment); el("memberSearch").addEventListener("input",()=>{clearTimeout(timer);timer=setTimeout(loadMembers,250)}); el("memberRole").addEventListener("change",loadMembers); el("memberActive").addEventListener("change",loadMembers); el("reviewFilter").addEventListener("change",loadEvents);el("financeSearch").addEventListener("input",()=>{clearTimeout(timer);timer=setTimeout(loadFinancialCases,250)});el("financeStatus").addEventListener("change",loadFinancialCases);el("financeSeverity").addEventListener("change",loadFinancialCases);el("financeAssigned").addEventListener("change",loadFinancialCases);el("analyticsDays").addEventListener("change",loadAnalytics);el("auditSearch").addEventListener("input",()=>{clearTimeout(timer);timer=setTimeout(()=>loadAuditLogs(0),250)});el("auditAction").addEventListener("input",()=>{clearTimeout(timer);timer=setTimeout(()=>loadAuditLogs(0),250)});el("auditResourceType").addEventListener("input",()=>{clearTimeout(timer);timer=setTimeout(()=>loadAuditLogs(0),250)});el("auditLoadMore").addEventListener("click",()=>{if(state.auditNextOffset!=null)loadAuditLogs(state.auditNextOffset).catch(err=>alert(err.message));});
  el("productList").addEventListener("click",e=>{const id=e.target.closest("[data-edit-product]")?.dataset.editProduct;if(id) openProduct(state.products.find(p=>p.id===id));});
  el("financialCaseList").addEventListener("click",async e=>{const claim=e.target.closest("[data-claim-finance]")?.dataset.claimFinance;const release=e.target.closest("[data-release-finance]")?.dataset.releaseFinance;const resolve=e.target.closest("[data-resolve-finance]")?.dataset.resolveFinance;try{if(claim)await claimFinancialCase(claim);else if(release)await releaseFinancialCase(release);else if(resolve)openFinancialResolution(resolve);}catch(err){alert(err.message||"Financial operation failed.");}});el("financialResolutionForm").addEventListener("submit",e=>resolveFinancialCase(e).catch(err=>alert(err.message)));
  el("fulfillmentList").addEventListener("click",async e=>{const requeue=e.target.closest("[data-requeue-job]")?.dataset.requeueJob;const reconcile=e.target.closest("[data-reconcile-order]")?.dataset.reconcileOrder;try{if(requeue) await requeueJob(requeue);else if(reconcile) await reconcileOrder(reconcile);}catch(error){alert(error.message||"Fulfillment operation failed.");}});
  el("newSupplier").addEventListener("click",openSupplier);el("supplierType").addEventListener("change",refreshSupplierAdapterFields);el("supplierForm").addEventListener("submit",e=>saveSupplier(e).catch(err=>alert(err.message)));el("configurePayment").addEventListener("click",()=>openPaymentProvider());el("paymentProviderForm").addEventListener("submit",e=>savePaymentProvider(e).catch(err=>alert(err.message)));el("paymentProviderName").addEventListener("change",e=>{const name=e.target.value;if(!state.paymentProviders.some(p=>p.provider_name===name))el("paymentProviderSettings").value=JSON.stringify(defaultPaymentSettings(name),null,2);});el("newMapping").addEventListener("click",()=>{try{openMapping();}catch(err){alert(err.message);}});el("mappingForm").addEventListener("submit",e=>saveMapping(e).catch(err=>alert(err.message)));el("mappingProduct").addEventListener("change",refreshMappingVariants);
  el("supplierProviderList").addEventListener("click",async e=>{const health=e.target.closest("[data-health-provider]")?.dataset.healthProvider;const toggle=e.target.closest("[data-toggle-provider]")?.dataset.toggleProvider;try{if(health)await healthSupplier(health);else if(toggle)await toggleSupplier(toggle);}catch(err){alert(err.message||"Provider operation failed.");}});el("paymentProviderList").addEventListener("click",e=>{const name=e.target.closest("[data-edit-payment-provider]")?.dataset.editPaymentProvider;if(name)openPaymentProvider(name);});el("memberList").addEventListener("click",e=>{const id=e.target.closest("[data-edit-member]")?.dataset.editMember;if(id){const member=state.members.find(m=>m.id===id);if(member)openMember(member);}});el("memberForm").addEventListener("submit",e=>saveMember(e).catch(err=>alert(err.message)));
  el("unlockSalesBtn")?.addEventListener("click", () => {
    el("salesOperatorGate")?.classList.toggle("hidden");
    el("platformTokenInput")?.focus();
  });
  el("submitPlatformToken")?.addEventListener("click", async () => {
    const val = el("platformTokenInput")?.value.trim();
    if (!val) {
      alert("Enter platform operator token.");
      return;
    }
    state.platformToken = val;
    await loadSales();
  });
  document.querySelectorAll("[data-sales-tab]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      document.querySelectorAll("[data-sales-tab]").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      state.salesActiveTab = btn.dataset.salesTab;
      await loadSales();
    });
  });
  el("salesSearch")?.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(loadSales, 250);
  });
  el("salesStatusFilter")?.addEventListener("change", loadSales);
  el("salesInquiryList")?.addEventListener("click", (e) => {
    const id = e.target.closest("[data-inspect-inquiry]")?.dataset.inspectInquiry;
    if (id) openInquiryDetail(id);
  });
  el("salesQuotesList")?.addEventListener("click", (e) => {
    const acceptId = e.target.closest("[data-accept-quote]")?.dataset.acceptQuote;
    const onboardId = e.target.closest("[data-onboard-quote]")?.dataset.onboardQuote;
    if (acceptId) acceptQuote(acceptId);
    else if (onboardId) openOnboardTenantDialog(onboardId);
  });
  el("onboardTenantForm")?.addEventListener("submit", saveOnboardTenant);
  document.querySelectorAll("[data-close-onboard-tenant]").forEach((b) => b.addEventListener("click", () => el("onboardTenantDialog").close()));
  el("onboardingItemsList")?.addEventListener("click", (e) => {
    const target = e.target.closest("[data-onboarding-target]")?.dataset.onboardingTarget;
    if (target) {
      const navBtn = document.querySelector(`.nav[data-view="${target}"]`);
      if (navBtn) navBtn.click();
    }
  });
  el("saveInquiryStatusBtn")?.addEventListener("click", async () => {
    const inqId = el("inquiryDetailId").value;
    const newStatus = el("inquiryUpdateStatus").value;
    try {
      await platformApi(`/api/v1/platform/sales/inquiries/${inqId}/status`, {
        method: "PATCH",
        body: JSON.stringify({ status: newStatus }),
      });
      alert(`Inquiry status updated to ${newStatus}.`);
      await loadSales();
      openInquiryDetail(inqId);
    } catch (err) {
      alert(err.message);
    }
  });
  el("openCreateQuoteFromInquiryBtn")?.addEventListener("click", () => {
    openCreateQuoteDialog(state.currentInquiry);
  });
  el("addQuoteLineBtn")?.addEventListener("click", () => {
    const container = el("quoteLineItemsContainer");
    const row = document.createElement("div");
    row.className = "grid2 quote-line-row";
    row.style = "background:#0c1322;padding:8px;border-radius:8px;border:1px solid var(--line);margin-bottom:6px";
    row.innerHTML = `
      <div>
        <input type="text" class="quote-line-name" placeholder="Item name" required>
        <input type="text" class="quote-line-desc" placeholder="Description" style="margin-top:4px;font-size:12px">
      </div>
      <div style="display:flex;gap:6px">
        <select class="quote-line-type">
          <option value="one_time">One-time</option>
          <option value="recurring">Monthly</option>
        </select>
        <input type="number" step="0.01" min="0" class="quote-line-amount" value="50.00" style="width:100px" required>
        <button type="button" class="icon remove-quote-line" title="Remove line">×</button>
      </div>
    `;
    container.appendChild(row);
  });
  el("quoteLineItemsContainer")?.addEventListener("click", (e) => {
    if (e.target.closest(".remove-quote-line")) {
      e.target.closest(".quote-line-row")?.remove();
    }
  });
  el("createQuoteForm")?.addEventListener("submit", saveCreateQuote);
  document.querySelectorAll("[data-close-inquiry-detail]").forEach((b) => b.addEventListener("click", () => el("inquiryDetailDialog").close()));
  document.querySelectorAll("[data-close-create-quote]").forEach((b) => b.addEventListener("click", () => el("createQuoteDialog").close()));
  el("openIntegrationsMarketplaceBtn")?.addEventListener("click", () => {
    loadAdminIntegrations().catch((err) => alert(err.message));
    el("integrationsMarketplaceDialog").showModal();
  });
  document.querySelectorAll("[data-close-integrations-marketplace]").forEach((b) => b.addEventListener("click", () => el("integrationsMarketplaceDialog").close()));
  el("marketplaceIntegrationsList")?.addEventListener("click", (e) => {
    const key = e.target.closest("[data-configure-integration]")?.dataset.configureIntegration;
    if (key) openConfigureIntegrationModal(key);
  });
  el("configureMarketplaceIntegrationForm")?.addEventListener("submit", saveConfigureIntegration);
  document.querySelectorAll("[data-close-configure-marketplace]").forEach((b) => b.addEventListener("click", () => el("configureMarketplaceIntegrationDialog").close()));
}

function handleSignOut(event) {
  if (event) {
    try { event.preventDefault(); event.stopPropagation(); } catch (_) {}
  }
  clearSessionToken();
  showLogin("You have signed out.");
}
window.handleSignOut = handleSignOut;

el("loginForm").addEventListener("submit", signIn);
el("signOut")?.addEventListener("click", handleSignOut);
el("headerSignOut")?.addEventListener("click", handleSignOut);

async function initSession() {
  const urlParams = new URLSearchParams(location.search);
  const hashStr = location.hash.startsWith("#") ? location.hash.slice(1) : location.hash;
  const hashParams = new URLSearchParams(hashStr);
  const urlCode = (urlParams.get("code") || hashParams.get("code") || "").trim();

  if (urlCode) {
    try {
      urlParams.delete("code");
      const cleanSearch = urlParams.toString() ? `?${urlParams.toString()}` : "";
      window.history.replaceState({}, document.title, location.pathname + cleanSearch);
    } catch (_) {}

    try {
      const result = await api("/api/v1/auth/admin-code", {
        method: "POST",
        body: JSON.stringify({ code: urlCode }),
      });
      setSessionToken(result.access_token);
      await openConsole();
      return;
    } catch (error) {
      showLogin(error.message || "Sign-in link is invalid or expired. Send /admin to your bot for a new one.");
      return;
    }
  }

  if (await authenticate()) {
    setSessionToken(state.token);
    await openConsole();
    return;
  }

  const storedToken = getStoredToken();
  if (storedToken) {
    state.token = storedToken;
    try {
      await openConsole();
      return;
    } catch (error) {
      clearSessionToken();
      showLogin("Session expired. Please sign in again.");
      return;
    }
  }

  showLogin();
}

initSession().catch((error) => {
  showLogin(error.message || "Sign-in failed. Try a browser sign-in code below.");
});
