const $ = (id) => document.getElementById(id);
const form = $("setupForm");
let ready = false;
let submitted = false;
// Remove the one-time code from the address immediately, before any API request.
const initialCode = new URLSearchParams(location.search).get("code");
if (initialCode) $("setupCode").value = initialCode;
if (location.search)
  history.replaceState({}, document.title, location.pathname);
if (location.protocol === "https:") $("publicBaseUrl").value = location.origin;

function showError(message) {
  $("errorBox").textContent = message;
  $("errorBox").classList.remove("hidden");
  $("errorBox").focus();
}
async function loadStatus() {
  ready = false;
  $("submitButton").disabled = true;
  for (const id of ["setupForm", "doneCard", "unavailableCard", "successCard"])
    $(id).classList.add("hidden");
  $("loadingCard").classList.remove("hidden");
  $("statusBadge").textContent = "Checking…";
  try {
    const response = await fetch("/api/v1/setup/status", { cache: "no-store" });
    if (!response.ok)
      throw new Error(
        "Setup status is temporarily unavailable. Please try again.",
      );
    const data = await response.json();
    if (data.initialized) {
      $("statusBadge").textContent = "Configured";
      $("doneCard").classList.remove("hidden");
      return;
    }
    ready = data.ready_for_setup === true;
    $("statusBadge").textContent = ready
      ? "Ready for setup"
      : "Setup code needed";
    $("prerequisiteBox").classList.toggle("hidden", ready);
    form.classList.remove("hidden");
    $("submitButton").disabled = !ready;
  } catch (error) {
    $("statusBadge").textContent = "Unavailable";
    $("statusError").textContent = error.message || "Cannot load setup status.";
    $("unavailableCard").classList.remove("hidden");
  } finally {
    $("loadingCard").classList.add("hidden");
  }
}
$("retryStatus").addEventListener("click", loadStatus);
$("togglePassword").addEventListener("click", () => {
  const visible = $("ownerPassword").type === "password";
  $("ownerPassword").type = visible ? "text" : "password";
  $("togglePassword").textContent = visible ? "Hide" : "Show";
  $("togglePassword").setAttribute("aria-pressed", String(visible));
  $("togglePassword").setAttribute(
    "aria-label",
    visible ? "Hide password" : "Show password",
  );
});
let slugEdited = false;
$("tenantSlug").addEventListener("input", () => {
  slugEdited = true;
});
$("tenantName").addEventListener("input", () => {
  if (!slugEdited)
    $("tenantSlug").value = $("tenantName")
      .value.toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-|-$/g, "")
      .slice(0, 100);
});
form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!ready || submitted) return;
  $("errorBox").classList.add("hidden");
  if ($("ownerPassword").value !== $("confirmPassword").value) {
    showError("Your passwords don’t match. Please enter them again.");
    return;
  }
  submitted = true;
  $("submitButton").disabled = true;
  $("submitButton").textContent = "Creating your factory…";
  try {
    const response = await fetch("/api/v1/setup/initialize-web", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        setup_code: $("setupCode").value,
        tenant_slug: $("tenantSlug").value,
        tenant_name: $("tenantName").value,
        username: $("ownerUsername").value,
        password: $("ownerPassword").value,
        public_base_url: $("publicBaseUrl").value || null,
      }),
    });
    const data = await response.json();
    if (!response.ok) {
      if (data.detail?.code === "ALREADY_INITIALIZED") {
        await loadStatus();
        return;
      }
      throw new Error(
        data.detail?.message ||
          (typeof data.detail === "string"
            ? data.detail
            : "Check your details and try again."),
      );
    }
    $("successText").textContent =
      `Sign in as ${$("ownerUsername").value.trim()} to review customer requests and start building.`;
    form.reset();
    form.classList.add("hidden");
    $("statusBadge").textContent = "Configured";
    $("successCard").classList.remove("hidden");
    $("successCard").focus();
    ready = false;
  } catch (error) {
    showError(
      error.message ||
        "Setup could not finish. Try again; your workspace details are still here.",
    );
  } finally {
    $("ownerPassword").value = "";
    $("confirmPassword").value = "";
    submitted = false;
    $("submitButton").disabled = !ready;
    $("submitButton").textContent = "Create my factory ↗";
  }
});
loadStatus();
