const state = {
  templates: [],
  integrations: [],
  selectedFormat: "combo",
  selectedTemplate: "general-commerce",
  selectedSource: "stored",
  selectedHosting: "managed",
  selectedIntegrations: new Set(),
  templateFilter: "all",
  templateQuery: "",
  currentEstimate: null,
};

const el = (id) => document.getElementById(id);
const escapeHtml = (v) =>
  String(v ?? "").replace(/[&<>'"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[c]));

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...options.headers },
    ...options,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || "Request failed.");
  }
  return res.json();
}

async function init() {
  try {
    const [tRes, iRes] = await Promise.all([
      api("/api/v1/public/templates"),
      api("/api/v1/public/integrations"),
    ]);
    state.templates = tRes.templates || [];
    state.integrations = iRes.integrations || [];

    restoreChoices();
    renderTemplates();
    if(StoreSetup.groupFor(state.selectedTemplate).id==="numbers")state.selectedSource="provider_api";
    if(StoreSetup.groupFor(state.selectedTemplate).id==="services")state.selectedSource="stored";
    renderIntegrations();
    syncChoices();
    renderDemo();
    bindEvents();
    await updateEstimate();
  } catch (error) {
    console.error("Initialization failed:", error);
    el("templatesGrid").innerHTML = `<div class="status-msg error">Failed to load templates: ${escapeHtml(error.message)}</div>`;
  }
}

const DRAFT_KEY = "ghbf_build_choices_v1";
function renderTemplates() {
  StoreSetup.chooser(el("templatesGrid"), {
    templates:state.templates, key:state.selectedTemplate, source:state.selectedSource,
    onChange:(key,source)=>{state.selectedTemplate=key;state.selectedSource=source;renderDemo();updateEstimate();},
  });
}
function renderDemo() {
  StoreSetup.preview(el("storeDemo"), {key:state.selectedTemplate,name:el("previewName").value,accent:el("previewAccent").value});
}
function saveChoices() {
  const saved=StoreSetup.saveDraft(DRAFT_KEY, {template:state.selectedTemplate,source:state.selectedSource,format:state.selectedFormat,hosting:state.selectedHosting,integrations:[...state.selectedIntegrations].filter(k=>k!=="custom-api-request"),name:el("previewName").value,accent:el("previewAccent").value});
  el("draftStatus").textContent=saved?"Choices saved in this tab for 24 hours. Contact details and notes are not saved.":"Browser storage is unavailable. Keep this tab open to preserve your choices.";
}
function restoreChoices() {
  const d=StoreSetup.readDraft(DRAFT_KEY);if(!d)return;
  if(state.templates.some(t=>t.key===d.template))state.selectedTemplate=d.template;
  if(["stored","provider_api","hybrid"].includes(d.source))state.selectedSource=d.source;
  if(["bot","miniapp","combo"].includes(d.format))state.selectedFormat=d.format;
  if(["managed","supabase_cloud","dedicated","source_license"].includes(d.hosting))state.selectedHosting=d.hosting;
  state.selectedIntegrations=new Set((Array.isArray(d.integrations)?d.integrations:[]).filter(k=>state.integrations.some(i=>i.key===k)));
  el("previewName").value=typeof d.name==="string"?d.name.slice(0,100):"";
  if(/^#[0-9a-f]{6}$/i.test(d.accent||""))el("previewAccent").value=d.accent;
}
function syncChoices() {
  for(const [container,value,dataKey] of [["formatChoices",state.selectedFormat,"format"],["hostingChoices",state.selectedHosting,"hosting"]]){
    el(container).querySelectorAll("input").forEach(n=>n.checked=n.value===value);
    el(container).querySelectorAll(".choice-card").forEach(n=>n.classList.toggle("active",n.dataset[dataKey]===value));
  }
  el("step-format").querySelector("summary").textContent=`Customer experience · ${{combo:"Bot + storefront",bot:"Bot only",miniapp:"Storefront"}[state.selectedFormat]}`;
  el("step-hosting").querySelector("summary").textContent=`Hosting · ${{managed:"We host it for you",supabase_cloud:"Your Supabase project",dedicated:"Dedicated server",source_license:"Source license"}[state.selectedHosting]}`;
  el("step-integrations").querySelector("summary").textContent=`Optional connections · ${state.selectedIntegrations.size} selected`;
}

function renderIntegrations() {
  const container = el("integrationsGrid");
  if (!state.integrations.length) {
    container.innerHTML = `<div class="muted">No optional integrations available.</div>`;
    return;
  }

  container.innerHTML = state.integrations
    .map((item) => {
      const isChecked = state.selectedIntegrations.has(item.key);
      return `
        <label class="integration-card ${isChecked ? "active" : ""}" data-integration-key="${escapeHtml(item.key)}">
          <input type="checkbox" value="${escapeHtml(item.key)}" ${isChecked ? "checked" : ""}>
          <div class="choice-body">
            <div class="choice-title-row">
              <strong>${escapeHtml(item.name)}</strong>
              <span class="price-tag">+$${escapeHtml(item.setup_fee)} setup · +$${escapeHtml(item.monthly_fee)}/mo</span>
            </div>
            <p class="muted">${escapeHtml(item.description)}</p>
          </div>
        </label>
      `;
    })
    .join("");
}

let estimateTimer = null;
let estimateRevision = 0;
async function updateEstimate() {
  clearTimeout(estimateTimer);
  const revision=++estimateRevision;
  state.currentEstimate=null;
  el("telegramChatBtn").hidden=true;
  el("estOneTime").textContent="Updating…";
  el("estMonthly").textContent="Updating…";
  el("estimateError").textContent="";
  saveChoices();
  syncChoices();
  estimateTimer = setTimeout(async () => {
    try {
      const payload = {
        format: state.selectedFormat,
        template_key: state.selectedTemplate,
        product_source: state.selectedSource,
        delivery_model: state.selectedHosting,
        integration_keys: Array.from(state.selectedIntegrations),
      };

      const est = await api("/api/v1/public/estimate", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      if(revision!==estimateRevision)return;
      state.currentEstimate = est;
      renderEstimateUI(est);
    } catch (err) {
      if(revision!==estimateRevision)return;
      el("estOneTime").textContent="Unavailable";
      el("estMonthly").textContent="Unavailable";
      el("itemizedReceipt").textContent="Change a choice to retry the estimate.";
      el("estimateError").textContent=`Could not update the estimate. ${err.message}`;
    }
  }, 100);
}

function renderEstimateUI(est) {
  el("estOneTime").textContent = `$${est.total_one_time}`;
  el("estMonthly").textContent = `$${est.total_monthly}`;

  const formatNames = {
    combo: "Bot + Mini App Combo",
    miniapp: "Telegram Mini App",
    bot: "Telegram Bot Only",
  };
  const sourceNames = {
    stored: "My own stock or services",
    provider_api: "Connected supplier",
    hybrid: "Stock and suppliers",
  };
  const hostingNames = {
    managed: "Managed Cloud Hosting",
    supabase_cloud: "Supabase Dedicated Cloud",
    dedicated: "Dedicated VPS Deployment",
    source_license: "Source Code Buyout License",
  };

  el("sumFormat").textContent = formatNames[est.format] || est.format;
  el("sumTemplate").textContent = est.template_name || est.template_key;
  el("sumSource").textContent = sourceNames[est.product_source] || est.product_source;
  el("sumHosting").textContent = hostingNames[est.delivery_model] || est.delivery_model;

  const intNames = est.selected_integrations
    .map((k) => state.integrations.find((i) => i.key === k)?.name || k)
    .join(", ");
  el("sumIntegrations").textContent = intNames || "None";

  el("estimateNote").textContent = est.notes || "Transparent server-calculated quote.";

  // Render itemized receipt
  el("itemizedReceipt").innerHTML = est.items
    .map(
      (it) => `
      <div class="receipt-row">
        <span>${escapeHtml(it.name)} (${it.item_type === "one_time" ? "setup" : "monthly"})</span>
        <strong>$${escapeHtml(it.amount)}</strong>
      </div>
    `
    )
    .join("");

  // Update direct Telegram button prefill
  const customReq = el("requestCustomApiCheck")?.checked ? el("customApiDescription")?.value.trim() : "";
  const msg =
    `Hello! I configured a Telegram Bot on your configurator:\n\n` +
    `• Format: ${formatNames[est.format] || est.format}\n` +
    `• Template: ${est.template_name}\n` +
    `• Product Source: ${sourceNames[est.product_source] || est.product_source}\n` +
    `• Hosting: ${hostingNames[est.delivery_model] || est.delivery_model}\n` +
    (customReq ? `• Requested Custom API: ${customReq}\n` : "") +
    `• Estimated Total: $${est.total_one_time} setup + $${est.total_monthly}/mo\n` +
    `\nI'd like to review this with you and get started!`;
  el("telegramChatBtn").hidden = !est.telegram_contact_url;
  if (!est.telegram_contact_url) {
    el("telegramChatBtn").removeAttribute("href");
    return;
  }
  const contactUrl = new URL(est.telegram_contact_url);
  contactUrl.searchParams.set("text", msg);
  el("telegramChatBtn").href = contactUrl.toString();
  el("telegramChatBtn").removeAttribute("aria-disabled");
}

function bindEvents() {
  el("telegramChatBtn").addEventListener("click", (event) => {
    if (!el("telegramChatBtn").hasAttribute("href")) event.preventDefault();
  });
  // Format choice
  el("formatChoices").addEventListener("change", (e) => {
    state.selectedFormat = e.target.value;
    document.querySelectorAll("#formatChoices .choice-card").forEach((card) => {
      card.classList.toggle("active", card.dataset.format === state.selectedFormat);
    });
    updateEstimate();
  });

  ["previewName","previewAccent"].forEach(id=>el(id).addEventListener("input",()=>{renderDemo();saveChoices();}));
  el("discardDraft").addEventListener("click",()=>{
    if(!confirm("Clear your choices and start again?"))return;
    StoreSetup.clearDraft(DRAFT_KEY);location.reload();
  });

  // Integrations Grid
  el("integrationsGrid").addEventListener("change", (e) => {
    const key = e.target.value;
    if (e.target.checked) {
      state.selectedIntegrations.add(key);
    } else {
      state.selectedIntegrations.delete(key);
    }
    document.querySelectorAll("#integrationsGrid .integration-card").forEach((card) => {
      card.classList.toggle("active", state.selectedIntegrations.has(card.dataset.integrationKey));
    });
    updateEstimate();
  });

  // Custom API Request Toggle & Input
  el("requestCustomApiCheck")?.addEventListener("change", (e) => {
    const checked = e.target.checked;
    el("customApiFields")?.classList.toggle("hidden", !checked);
    if (checked) {
      state.selectedIntegrations.add("custom-api-request");
      el("customApiDescription")?.focus();
    } else {
      state.selectedIntegrations.delete("custom-api-request");
    }
    updateEstimate();
  });

  el("customApiDescription")?.addEventListener("input", () => {
    if (state.currentEstimate) {
      renderEstimateUI(state.currentEstimate);
    }
  });

  // Hosting Choice
  el("hostingChoices").addEventListener("change", (e) => {
    state.selectedHosting = e.target.value;
    document.querySelectorAll("#hostingChoices .choice-card").forEach((card) => {
      card.classList.toggle("active", card.dataset.hosting === state.selectedHosting);
    });
    updateEstimate();
  });

  // Jump to Submit
  el("jumpToSubmit").addEventListener("click", () => {
    el("step-submit").scrollIntoView({ behavior: "smooth" });
  });

  // Inquiry Form Submission
  el("inquiryForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = el("submitInquiryBtn");
    const statusMsg = el("inquiryStatusMsg");
    btn.disabled = true;
    btn.textContent = "Submitting Inquiry...";
    statusMsg.className = "status-msg hidden";

    try {
      if(!state.currentEstimate)throw new Error("Wait for the estimate to update before sending. If it is unavailable, change a choice to retry.");
      const customApiChecked = el("requestCustomApiCheck")?.checked;
      const customApiText = el("customApiDescription")?.value.trim() || "";
      const customApiRequest = customApiChecked && customApiText ? customApiText : null;

      const payload = {
        contact_method: el("contactMethod").value,
        contact_handle: el("contactHandle").value.trim(),
        project_notes: [el("projectNotes").value.trim(), `Appearance preference: ${el("previewName").value.trim() || "Not named yet"}; accent ${el("previewAccent").value}.`].filter(Boolean).join("\n"),
        format: state.selectedFormat,
        template_key: state.selectedTemplate,
        product_source: state.selectedSource,
        delivery_model: state.selectedHosting,
        integration_keys: Array.from(state.selectedIntegrations),
        custom_api_request: customApiRequest,
      };
      const result = await api("/api/v1/public/inquiries", {
        method: "POST",
        body: JSON.stringify(payload),
      });

      StoreSetup.clearDraft(DRAFT_KEY);
      el("draftStatus").textContent="Request sent. Your saved draft has been cleared.";
      statusMsg.className = "status-msg success";
      statusMsg.innerHTML = `
        <strong>✓ Inquiry #${result.inquiry_id.slice(0, 8)} submitted successfully!</strong>
        <p style="margin: 4px 0 0">Your request has been saved for review.${result.telegram_link ? " You can also chat directly on Telegram now:" : ""}</p>
      `;

      if (result.telegram_link) {
        el("telegramChatBtn").hidden = false;
        el("telegramChatBtn").href = result.telegram_link;
        el("telegramChatBtn").removeAttribute("aria-disabled");
        el("telegramChatBtn").classList.add("pulse");
      }

      btn.textContent = "✓ Inquiry Sent";
    } catch (err) {
      statusMsg.className = "status-msg error";
      statusMsg.textContent = `Error: ${err.message || "Could not submit inquiry."}`;
      statusMsg.focus();
      btn.disabled = false;
      btn.textContent = "Send request";
    }
  });
}

document.addEventListener("DOMContentLoaded", init);
