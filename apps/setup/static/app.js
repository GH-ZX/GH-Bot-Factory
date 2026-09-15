const $ = (id) => document.getElementById(id);
const form = $("setupForm");
const doneCard = $("doneCard");
const successCard = $("successCard");
const badge = $("statusBadge");
const errorBox = $("errorBox");
const submitButton = $("submitButton");

function showError(message) {
  errorBox.textContent = message;
  errorBox.classList.remove("hidden");
}

async function loadStatus() {
  try {
    const response = await fetch("/api/v1/setup/status", {cache: "no-store"});
    const data = await response.json();
    if (!response.ok) throw new Error("Setup status is unavailable.");
    if (data.initialized) {
      badge.textContent = "Configured";
      doneCard.classList.remove("hidden");
      return;
    }
    badge.textContent = data.ready_for_setup ? "Ready for setup" : "Setup prerequisites missing";
    form.classList.remove("hidden");
    const params = new URLSearchParams(window.location.search);
    if (params.get("code")) $("setupCode").value = params.get("code");
    for (const template of data.templates || []) {
      const option = document.createElement("option");
      option.value = template.key;
      option.textContent = `${template.name} — ${template.description}`;
      $("template").appendChild(option);
    }
  } catch (error) {
    badge.textContent = "Unavailable";
    form.classList.remove("hidden");
    showError(error.message || "Cannot load setup status.");
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  errorBox.classList.add("hidden");
  submitButton.disabled = true;
  submitButton.textContent = "Verifying Telegram and configuring…";
  const payload = {
    setup_code: $("setupCode").value,
    tenant_slug: $("tenantSlug").value,
    tenant_name: $("tenantName").value,
    owner_telegram_id: Number($("ownerId").value),
    owner_username: $("ownerUsername").value || null,
    bot_token: $("botToken").value,
    expected_bot_username: $("botUsername").value || null,
    bot_display_name: $("botDisplayName").value,
    template_key: $("template").value || "general-commerce",
    public_base_url: $("publicBaseUrl").value || null,
  };
  try {
    const response = await fetch("/api/v1/setup/initialize", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) {
      const detail = data.detail;
      throw new Error(detail?.message || (typeof detail === "string" ? detail : "Setup failed."));
    }
    form.classList.add("hidden");
    successCard.classList.remove("hidden");
    badge.textContent = "Configured";
    $("successText").textContent = `Telegram bot @${data.telegram_username || data.telegram_bot_id} is registered. Runtime reconciliation runs every ${data.runtime_reconciliation_seconds}s.`;
    window.history.replaceState({}, document.title, "/setup/");
  } catch (error) {
    showError(error.message || "Setup failed.");
  } finally {
    submitButton.disabled = false;
    submitButton.textContent = "Initialize GH Bot Factory";
  }
});

loadStatus();
