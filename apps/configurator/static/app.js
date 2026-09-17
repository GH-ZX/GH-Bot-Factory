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

    renderTemplates();
    renderIntegrations();
    bindEvents();
    await updateEstimate();
  } catch (error) {
    console.error("Initialization failed:", error);
    el("templatesGrid").innerHTML = `<div class="status-msg error">Failed to load templates: ${escapeHtml(error.message)}</div>`;
  }
}

function renderTemplates() {
  const container = el("templatesGrid");
  const q = state.templateQuery.trim().toLowerCase();

  const filtered = state.templates.filter((t) => {
    const g = t.guidance || {};
    if (state.templateFilter !== "all" && g.product_source !== state.templateFilter) {
      return false;
    }
    if (!q) return true;
    const haystack = [t.name, t.key, t.description, t.recommended_for, g.what_you_can_sell, g.example_business]
      .filter(Boolean)
      .join(" ")
      .toLowerCase();
    return haystack.includes(q);
  });

  if (!filtered.length) {
    container.innerHTML = `<div class="muted" style="grid-column: 1 / -1; padding: 20px; text-align: center;">No templates match your search.</div>`;
    return;
  }

  const complexityClasses = { Low: "low", Medium: "medium", High: "high" };
  const sourceBadges = {
    stored: "📦 Stored",
    provider_api: "⚡ Live APIs",
    hybrid: "🔀 Hybrid",
  };

  container.innerHTML = filtered
    .map((t) => {
      const isSelected = t.key === state.selectedTemplate;
      const g = t.guidance || {};
      const compClass = complexityClasses[g.operational_complexity] || "medium";
      const sourceLabel = sourceBadges[g.product_source] || g.product_source || "Stored";

      return `
        <article class="template-card ${isSelected ? "active" : ""}" data-template-key="${escapeHtml(t.key)}">
          <div>
            <div class="template-card-head">
              <h4>${escapeHtml(t.name)}</h4>
              <span class="badge-source">${escapeHtml(sourceLabel)}</span>
            </div>
            <p class="template-desc">${escapeHtml(t.description)}</p>
            ${
              g.what_you_can_sell
                ? `<div class="template-sellable"><strong>Sell:</strong> ${escapeHtml(g.what_you_can_sell)}</div>`
                : ""
            }
          </div>
          <div style="display: flex; align-items: center; justify-content: space-between; margin-top: 10px;">
            <span class="badge-complexity ${compClass}">${escapeHtml(g.operational_complexity || "Low")} Complexity</span>
            <span style="font-size: 12px; font-weight: 700; color: ${isSelected ? "var(--accent)" : "var(--muted)"}">
              ${isSelected ? "✓ Selected" : "Select"}
            </span>
          </div>
        </article>
      `;
    })
    .join("");
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
async function updateEstimate() {
  clearTimeout(estimateTimer);
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
      state.currentEstimate = est;
      renderEstimateUI(est);
    } catch (err) {
      console.error("Quote estimation error:", err);
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
    stored: "Stored Manual Inventory",
    provider_api: "Live Wholesale APIs",
    hybrid: "Hybrid (Stored + APIs)",
  };
  const hostingNames = {
    managed: "Managed Cloud Hosting",
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
  const msg =
    `Hello! I configured a Telegram Bot on your configurator:\n\n` +
    `• Format: ${formatNames[est.format] || est.format}\n` +
    `• Template: ${est.template_name}\n` +
    `• Product Source: ${sourceNames[est.product_source] || est.product_source}\n` +
    `• Hosting: ${hostingNames[est.delivery_model] || est.delivery_model}\n` +
    `• Estimated Total: $${est.total_one_time} setup + $${est.total_monthly}/mo\n` +
    `\nI'd like to review this with you and get started!`;

  el("telegramChatBtn").href = `https://t.me/GH_Store?text=${encodeURIComponent(msg)}`;
}

function bindEvents() {
  // Format choice
  el("formatChoices").addEventListener("change", (e) => {
    state.selectedFormat = e.target.value;
    document.querySelectorAll("#formatChoices .choice-card").forEach((card) => {
      card.classList.toggle("active", card.dataset.format === state.selectedFormat);
    });
    updateEstimate();
  });

  // Template Search & Filter
  el("templateSearch").addEventListener("input", (e) => {
    state.templateQuery = e.target.value;
    renderTemplates();
  });

  document.querySelectorAll(".filter-chips button").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".filter-chips button").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      state.templateFilter = btn.dataset.filter;
      renderTemplates();
    });
  });

  // Template Card Selection
  el("templatesGrid").addEventListener("click", (e) => {
    const card = e.target.closest("[data-template-key]");
    if (card) {
      state.selectedTemplate = card.dataset.templateKey;
      const t = state.templates.find((x) => x.key === state.selectedTemplate);
      // Auto-switch source matching template guidance if appropriate
      if (t?.guidance?.product_source) {
        state.selectedSource = t.guidance.product_source;
        document.querySelectorAll("#sourceChoices input").forEach((r) => {
          r.checked = r.value === state.selectedSource;
        });
        document.querySelectorAll("#sourceChoices .choice-card").forEach((c) => {
          c.classList.toggle("active", c.dataset.source === state.selectedSource);
        });
      }
      renderTemplates();
      updateEstimate();
    }
  });

  // Source Choice
  el("sourceChoices").addEventListener("change", (e) => {
    state.selectedSource = e.target.value;
    document.querySelectorAll("#sourceChoices .choice-card").forEach((card) => {
      card.classList.toggle("active", card.dataset.source === state.selectedSource);
    });
    updateEstimate();
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
      const payload = {
        contact_method: el("contactMethod").value,
        contact_handle: el("contactHandle").value.trim(),
        project_notes: el("projectNotes").value.trim() || null,
        format: state.selectedFormat,
        template_key: state.selectedTemplate,
        product_source: state.selectedSource,
        delivery_model: state.selectedHosting,
        integration_keys: Array.from(state.selectedIntegrations),
      };

      const result = await api("/api/v1/public/inquiries", {
        method: "POST",
        body: JSON.stringify(payload),
      });

      statusMsg.className = "status-msg success";
      statusMsg.innerHTML = `
        <strong>✓ Inquiry #${result.inquiry_id.slice(0, 8)} submitted successfully!</strong>
        <p style="margin: 4px 0 0">The Factory Owner has received your request. You can also chat directly on Telegram now:</p>
      `;

      if (result.telegram_link) {
        el("telegramChatBtn").href = result.telegram_link;
        el("telegramChatBtn").classList.add("pulse");
      }

      btn.textContent = "✓ Inquiry Sent";
    } catch (err) {
      statusMsg.className = "status-msg error";
      statusMsg.textContent = `Error: ${err.message || "Could not submit inquiry."}`;
      btn.disabled = false;
      btn.textContent = "🚀 Send Project Inquiry";
    }
  });
}

document.addEventListener("DOMContentLoaded", init);
