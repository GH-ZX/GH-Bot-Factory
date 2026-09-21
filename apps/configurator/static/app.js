/* Public project brief. Choices express customer intent, never entitlement or price authority. */
const FEATURES = [
  ["pricing", "Margins & fixed prices", "Choose how you price your catalog."],
  [
    "warranty",
    "Warranty & replacements",
    "A clear process when something goes wrong.",
  ],
  ["coupons", "Coupons & offers", "Promotions for new and returning shoppers."],
  [
    "resellers",
    "Reseller pricing",
    "Different prices for your business partners.",
  ],
  ["support", "Support tickets", "Keep customer questions in one place."],
  [
    "announcements",
    "Announcements",
    "Share updates and offers with your audience.",
  ],
  [
    "branding",
    "Images & custom messages",
    "Your product images, wording and welcome.",
  ],
  [
    "catalog",
    "Catalog organization",
    "Rename and reorder products and categories.",
  ],
  ["users", "Customer management", "Manage access and customer options."],
  [
    "history",
    "Activity & order history",
    "Follow purchases and important changes.",
  ],
  [
    "review",
    "Purchase review tools",
    "Safely investigate orders needing attention.",
  ],
  ["alerts", "Important alerts", "Stay informed about orders and suppliers."],
  [
    "motion",
    "Polished animations",
    "Thoughtful transitions and visual feedback.",
  ],
  [
    "emoji",
    "Telegram custom emoji",
    "Where supported for your bot and account.",
  ],
];
const FORMATS = {
  combo: "Bot + Mini App",
  bot: "Telegram bot",
  miniapp: "Telegram Mini App",
};
const HOSTING = {
  supabase_cloud: "Your server + Supabase",
  dedicated: "Your server + PostgreSQL",
};
const DRAFT_KEY = "ghbf_build_brief_v2";
const state = {
  templates: [],
  integrations: [],
  selectedTemplate: "general-commerce",
  selectedSource: "stored",
  selectedFormat: "combo",
  selectedHosting: "supabase_cloud",
  selectedIntegrations: new Set(),
  features: new Set(),
  step: 0,
  ready: false,
  submitted: false,
};
const el = (id) => document.getElementById(id);
const escapeHtml = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
async function api(path, options = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 20000);
  try {
    const response = await fetch(`/api/v1/public/${path}`, {
      ...options,
      headers: { "Content-Type": "application/json" },
      signal: controller.signal,
    });
    if (!response.ok)
      throw new Error(
        response.status === 429
          ? "Too many requests. Please wait a moment and try again."
          : "We couldn’t complete that request. Please try again.",
      );
    return await response.json();
  } catch (error) {
    if (error.name === "AbortError")
      throw new Error("The connection timed out. Please try again.");
    throw error;
  } finally {
    clearTimeout(timer);
  }
}
function selectedStyle() {
  return document.querySelector('[name="visual_style"]:checked').value;
}
function featureNames() {
  return FEATURES.filter(([key]) => state.features.has(key)).map(
    ([, name]) => name,
  );
}
function saveChoices() {
  if (state.submitted) return;
  const saved = StoreSetup.saveDraft(DRAFT_KEY, {
    template: state.selectedTemplate,
    source: state.selectedSource,
    format: state.selectedFormat,
    hosting: state.selectedHosting,
    integrations: [...state.selectedIntegrations],
    features: [...state.features],
    name: el("previewName").value,
    accent: el("previewAccent").value,
    language: el("storeLanguage").value,
    report: el("reportLanguage").value,
    style: selectedStyle(),
    advice: el("hostingAdvice").checked,
  });
  el("draftStatus").textContent = saved
    ? "Choices saved in this tab. Contact details and notes aren’t saved."
    : "Draft saving is unavailable. Keep this tab open.";
}
function restoreChoices() {
  const draft = StoreSetup.readDraft(DRAFT_KEY);
  if (!draft) return;
  if (state.templates.some((t) => t.key === draft.template))
    state.selectedTemplate = draft.template;
  if (["stored", "provider_api", "hybrid"].includes(draft.source))
    state.selectedSource = draft.source;
  if (Object.hasOwn(FORMATS, draft.format)) state.selectedFormat = draft.format;
  if (Object.hasOwn(HOSTING, draft.hosting))
    state.selectedHosting = draft.hosting;
  state.selectedIntegrations = new Set(
    (Array.isArray(draft.integrations) ? draft.integrations : []).filter((k) =>
      state.integrations.some((i) => i.key === k),
    ),
  );
  state.features = new Set(
    (Array.isArray(draft.features) ? draft.features : []).filter((k) =>
      FEATURES.some(([key]) => key === k),
    ),
  );
  el("previewName").value =
    typeof draft.name === "string" ? draft.name.slice(0, 100) : "";
  if (/^#[0-9a-f]{6}$/i.test(draft.accent || ""))
    el("previewAccent").value = draft.accent;
  for (const [id, key] of [
    ["storeLanguage", "language"],
    ["reportLanguage", "report"],
  ])
    if ([...el(id).options].some((o) => o.value === draft[key]))
      el(id).value = draft[key];
  document.querySelectorAll('[name="visual_style"]').forEach((input) => {
    if (input.value === draft.style) input.checked = true;
  });
  el("hostingAdvice").checked = draft.advice === true;
}
function normalizeSource() {
  const group = StoreSetup.groupFor(state.selectedTemplate).id;
  if (group === "numbers") state.selectedSource = "provider_api";
  if (group === "services") state.selectedSource = "stored";
}
function renderFeatures() {
  el("featuresGrid").innerHTML = FEATURES.map(
    ([key, name, description]) =>
      `<label class="feature"><input type="checkbox" value="${key}" ${state.features.has(key) ? "checked" : ""}><span><strong>${name}</strong><small>${description}</small></span></label>`,
  ).join("");
}
function renderIntegrations() {
  el("integrationsGrid").innerHTML = state.integrations.length
    ? state.integrations
        .map((item) => {
          const matched =
            !item.supported_templates?.length ||
            item.supported_templates.includes(state.selectedTemplate);
          return `<label class="integration-card"><input type="checkbox" value="${escapeHtml(item.key)}" ${state.selectedIntegrations.has(item.key) ? "checked" : ""}><span><strong>${escapeHtml(item.name)}</strong><small>${item.category === "payment_gateway" ? "Payment connection" : "Supplier connection"} · scope confirmed after review</small>${matched ? "" : '<small class="compatibility">Compatibility with this store needs review.</small>'}</span></label>`;
        })
        .join("")
    : '<p class="muted">No connections are listed right now. Tell us what you need below.</p>';
}
function renderDemo() {
  StoreSetup.preview(el("storeDemo"), {
    key: state.selectedTemplate,
    name: el("previewName").value,
    accent: el("previewAccent").value,
  });
  el("accentValue").textContent = el("previewAccent").value.toUpperCase();
}
function updateSummary() {
  el("summaryName").textContent =
    el("previewName").value.trim() || "Your next store";
  el("sumFormat").textContent = FORMATS[state.selectedFormat];
  el("sumTemplate").textContent = StoreSetup.groupFor(
    state.selectedTemplate,
  ).name;
  el("sumHosting").textContent = HOSTING[state.selectedHosting];
  el("sumIntegrations").textContent = state.selectedIntegrations.size
    ? `${state.selectedIntegrations.size} requested`
    : "Let’s discuss";
  const names = state.integrations
    .filter((i) => state.selectedIntegrations.has(i.key))
    .map((i) => i.name);
  const custom = el("requestCustomApiCheck").checked
    ? el("customApiDescription").value.trim()
    : "";
  const rows = [
    [
      "Your store",
      `${el("previewName").value.trim() || "Name to be decided"} · ${FORMATS[state.selectedFormat]} · ${StoreSetup.groupFor(state.selectedTemplate).name}`,
      0,
    ],
    [
      "Product source",
      {
        stored: "My own stock or services",
        provider_api: "Connected suppliers",
        hybrid: "Stock and suppliers",
      }[state.selectedSource],
      0,
    ],
    [
      "Requested features",
      featureNames().join(", ") ||
        "Starting foundation; extra features to discuss",
      1,
    ],
    [
      "Connections",
      [...names, custom ? `Custom API: ${custom}` : ""]
        .filter(Boolean)
        .join(" · ") || "We’ll choose these together",
      1,
    ],
    [
      "Look & languages",
      `${selectedStyle()} · ${el("storeLanguage").value} · Guide: ${el("reportLanguage").value}`,
      2,
    ],
    [
      "Delivery",
      `${HOSTING[state.selectedHosting]}${el("hostingAdvice").checked ? " · Setup advice requested" : ""}`,
      2,
    ],
  ];
  el("reviewSummary").innerHTML = rows
    .map(
      ([label, value, step]) =>
        `<div class="review-row"><div><small>${label}</small><strong>${escapeHtml(value)}</strong></div><button type="button" class="text-button" data-edit="${step}" aria-label="Edit ${label.toLowerCase()}">Edit</button></div>`,
    )
    .join("");
}
function showStep(step, focus = true) {
  if (!state.ready) return;
  state.step = step;
  document
    .querySelectorAll("[data-panel]")
    .forEach((panel) => (panel.hidden = Number(panel.dataset.panel) !== step));
  document.querySelectorAll("[data-step]").forEach((button) => {
    if (Number(button.dataset.step) === step)
      button.setAttribute("aria-current", "step");
    else button.removeAttribute("aria-current");
  });
  el("previousStep").hidden = step === 0;
  el("nextStep").hidden = step === 3;
  el("stepCount").textContent = `Step ${step + 1} of 4`;
  el("nextStep").innerHTML =
    `${["Next: features", "Next: make it yours", "Review my brief"][step] || "Review"} <span aria-hidden="true">→</span>`;
  updateSummary();
  if (focus) {
    const heading = document.querySelector(`[data-panel="${step}"] h3`);
    heading.focus({ preventScroll: true });
    document.querySelector(".stepper").scrollIntoView({
      behavior: matchMedia("(prefers-reduced-motion: reduce)").matches
        ? "instant"
        : "smooth",
      block: "start",
    });
  }
}
function projectNotes() {
  return [
    `Store: ${el("previewName").value.trim() || "Not named yet"}; accent ${el("previewAccent").value}; design ${selectedStyle()}.`,
    `Languages: ${el("storeLanguage").value}; handoff guide: ${el("reportLanguage").value}.`,
    `Requested features (scope review required): ${featureNames().join(", ") || "Starting foundation"}.`,
    "Requested delivery: customer-owned Docker deployment, database setup/migrations, localized guide. One-time project quote requested; infrastructure/supplier costs separate.",
    el("hostingAdvice").checked ? "Customer requests server/setup advice." : "",
    el("projectNotes").value.trim(),
  ]
    .filter(Boolean)
    .join("\n");
}
function safeTelegramUrl(value) {
  try {
    const url = new URL(value);
    return url.protocol === "https:" &&
      url.hostname === "t.me" &&
      !url.username &&
      !url.password
      ? url.href
      : null;
  } catch (_) {
    return null;
  }
}
function bindEvents() {
  document
    .querySelectorAll("[data-step]")
    .forEach((button) =>
      button.addEventListener("click", () =>
        showStep(Number(button.dataset.step)),
      ),
    );
  el("nextStep").addEventListener("click", () =>
    showStep(Math.min(state.step + 1, 3)),
  );
  el("previousStep").addEventListener("click", () =>
    showStep(Math.max(state.step - 1, 0)),
  );
  el("jumpToSubmit").addEventListener("click", () => showStep(3));
  el("reviewSummary").addEventListener("click", (event) => {
    const button = event.target.closest("[data-edit]");
    if (button) showStep(Number(button.dataset.edit));
  });
  el("featuresGrid").addEventListener("change", (event) => {
    event.target.checked
      ? state.features.add(event.target.value)
      : state.features.delete(event.target.value);
    updateSummary();
    saveChoices();
  });
  el("integrationsGrid").addEventListener("change", (event) => {
    event.target.checked
      ? state.selectedIntegrations.add(event.target.value)
      : state.selectedIntegrations.delete(event.target.value);
    updateSummary();
    saveChoices();
  });
  for (const [name, key] of [
    ["format", "selectedFormat"],
    ["delivery_model", "selectedHosting"],
  ])
    document.querySelectorAll(`[name="${name}"]`).forEach((input) =>
      input.addEventListener("change", () => {
        state[key] = input.value;
        updateSummary();
        saveChoices();
      }),
    );
  document.querySelectorAll('[name="visual_style"]').forEach((input) =>
    input.addEventListener("change", () => {
      updateSummary();
      saveChoices();
    }),
  );
  for (const id of [
    "previewName",
    "previewAccent",
    "storeLanguage",
    "reportLanguage",
    "hostingAdvice",
  ])
    el(id).addEventListener("input", () => {
      if (id === "previewName" || id === "previewAccent") renderDemo();
      updateSummary();
      saveChoices();
    });
  el("requestCustomApiCheck").addEventListener("change", () => {
    el("customApiFields").hidden = !el("requestCustomApiCheck").checked;
    if (!el("customApiFields").hidden) el("customApiDescription").focus();
    updateSummary();
  });
  el("customApiDescription").addEventListener("input", updateSummary);
  el("discardDraft").addEventListener("click", () => {
    if (!confirm("Clear your choices and start again?")) return;
    StoreSetup.clearDraft(DRAFT_KEY);
    location.reload();
  });
  el("retryLoad").addEventListener("click", loadOptions);
  el("contactMethod").addEventListener("change", () => {
    const method = el("contactMethod").value;
    el("contactLabel").textContent = {
      TELEGRAM: "Telegram username",
      WHATSAPP: "Phone number with country code",
      EMAIL: "Email address",
    }[method];
    el("contactHandle").type =
      method === "EMAIL" ? "email" : method === "WHATSAPP" ? "tel" : "text";
    el("contactHandle").placeholder = {
      TELEGRAM: "@your_username",
      WHATSAPP: "+90 …",
      EMAIL: "you@example.com",
    }[method];
  });
  el("inquiryForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!state.ready || state.submitted || el("submitInquiryBtn").disabled)
      return;
    const button = el("submitInquiryBtn"),
      message = el("inquiryStatusMsg");
    button.disabled = true;
    button.textContent = "Sending your brief…";
    message.hidden = true;
    try {
      const contact = el("contactHandle").value.trim();
      if (contact.length < 2)
        throw new Error("Please enter a contact we can reply to.");
      const notes = projectNotes();
      if (notes.length > 2000)
        throw new Error("Please shorten your project notes before sending.");
      const result = await api("inquiries", {
        method: "POST",
        body: JSON.stringify({
          contact_method: el("contactMethod").value,
          contact_handle: contact,
          project_notes: notes,
          brief: {store_name: el("previewName").value.trim(), accent: el("previewAccent").value, visual_style: selectedStyle(), store_language: el("storeLanguage").value, report_language: el("reportLanguage").value, requested_features: [...state.features], hosting_advice: el("hostingAdvice").checked},
          format: state.selectedFormat,
          template_key: state.selectedTemplate,
          product_source: state.selectedSource,
          delivery_model: state.selectedHosting,
          integration_keys: [...state.selectedIntegrations],
          custom_api_request: el("requestCustomApiCheck").checked
            ? el("customApiDescription").value.trim() || null
            : null,
        }),
      });
      state.submitted = true;
      StoreSetup.clearDraft(DRAFT_KEY);
      el("draftStatus").textContent =
        "Brief received. Your saved draft has been cleared.";
      message.className = "success";
      message.textContent = `Your brief is in! Reference ${String(result.inquiry_id).slice(0, 8)}. We’ll review your choices and contact you to confirm the scope and quote.`;
      const link = safeTelegramUrl(result.telegram_link);
      if (link) {
        const contactUrl = new URL(link);
        contactUrl.searchParams.set(
          "text",
          `Hello! I submitted project brief #${String(result.inquiry_id).slice(0, 8)}. I’d like to review the scope and a one-time project quote for my customer-owned store.`,
        );
        el("telegramChatBtn").href = contactUrl.href;
        el("telegramChatBtn").hidden = false;
      }
      button.textContent = "Brief sent ✓";
      // The submitted brief is a snapshot. Avoid implying later edits update it.
      document
        .querySelectorAll(
          "#builderContent input,#builderContent select,#builderContent textarea,#featuresGrid button,#templatesGrid button",
        )
        .forEach((input) => (input.disabled = true));
      document
        .querySelectorAll("#reviewSummary [data-edit]")
        .forEach((input) => (input.disabled = true));
    } catch (error) {
      message.className = "error";
      message.textContent =
        error.message ||
        "We couldn’t send your brief. Your entries are still here; please try again.";
      button.disabled = false;
      button.textContent = "Send my project brief ↗";
    }
    message.hidden = false;
    message.focus();
  });
}
async function loadOptions() {
  el("retryLoad").disabled = true;
  el("loadError").hidden = true;
  try {
    const [templates, integrations] = await Promise.all([
      api("templates"),
      api("integrations"),
    ]);
    if (
      !Array.isArray(templates.templates) ||
      !templates.templates.length ||
      !Array.isArray(integrations.integrations)
    )
      throw new Error("Options are unavailable.");
    state.templates = templates.templates;
    state.integrations = integrations.integrations.filter(
      (item) => !["custom-api-request", "supabase"].includes(item.key),
    );
    restoreChoices();
    normalizeSource();
    StoreSetup.chooser(el("templatesGrid"), {
      templates: state.templates,
      key: state.selectedTemplate,
      source: state.selectedSource,
      onChange: (key, source) => {
        state.selectedTemplate = key;
        state.selectedSource = source;
        renderIntegrations();
        renderDemo();
        updateSummary();
        saveChoices();
      },
    });
    for (const [name, value] of [
      ["format", state.selectedFormat],
      ["delivery_model", state.selectedHosting],
    ])
      document
        .querySelectorAll(`[name="${name}"]`)
        .forEach((input) => (input.checked = input.value === value));
    renderFeatures();
    renderIntegrations();
    renderDemo();
    state.ready = true;
    showStep(0, false);
    el("nextStep").disabled = false;
    el("jumpToSubmit").disabled = false;
    el("builderContent").setAttribute("aria-busy", "false");
  } catch (_) {
    el("loadError").hidden = false;
    el("templatesGrid").textContent =
      "Store options are temporarily unavailable.";
    el("builderContent").setAttribute("aria-busy", "false");
  } finally {
    el("retryLoad").disabled = false;
  }
}
document.addEventListener("DOMContentLoaded", () => {
  bindEvents();
  renderDemo();
  loadOptions();
});
