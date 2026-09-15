const tg = window.Telegram?.WebApp ?? null;
const state = {
  token: null,
  botId: null,
  bootstrap: null,
  catalog: { categories: [], products: [], total: 0, has_more: false, next_offset: null },
  catalogQuery: "",
  catalogAvailable: null,
  catalogBusy: false,
  catalogRequestId: 0,
  variantCache: new Map(),
  orders: [],
  selectedCategory: null,
  cart: new Map(),
  checkoutKey: null,
  checkoutBusy: false,
  topupOptions: [],
  topupKey: null,
  topupBusy: false,
  activeTopup: null,
  topupPollTimer: null,
};

const el = (id) => document.getElementById(id);
const loadingView = el("loadingView");
const errorView = el("errorView");
const authenticatedApp = el("authenticatedApp");
const bottomNav = el("bottomNav");
const productGrid = el("productGrid");
const categoryRail = el("categoryRail");
const catalogSearchInput = el("catalogSearchInput");
const catalogAvailabilitySelect = el("catalogAvailabilitySelect");
const loadMoreProductsButton = el("loadMoreProductsButton");
const cartSheet = el("cartSheet");
const cartBackdrop = el("cartBackdrop");
const recipientInput = el("recipientInput");
const topupSheet = el("topupSheet");
const topupBackdrop = el("topupBackdrop");
const topupAmountInput = el("topupAmountInput");
const topupProviderSelect = el("topupProviderSelect");
const topupCurrencySelect = el("topupCurrencySelect");

function haptic(kind = "light") {
  try { tg?.HapticFeedback?.impactOccurred(kind); } catch (_) { /* no-op outside Telegram */ }
}

function notifyHaptic(kind = "success") {
  try { tg?.HapticFeedback?.notificationOccurred(kind); } catch (_) { /* no-op */ }
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function safeImageUrl(value) {
  if (!value) return null;
  try {
    const url = new URL(value, window.location.origin);
    return ["http:", "https:"].includes(url.protocol) ? url.href : null;
  } catch (_) {
    return null;
  }
}

function safeHttpsUrl(value) {
  if (!value) return null;
  try {
    const url = new URL(value);
    return url.protocol === "https:" && !url.username && !url.password ? url.href : null;
  } catch (_) {
    return null;
  }
}

function money(value, currency) {
  const amount = Number(value ?? 0);
  if (currency === "XTR") return `⭐ ${Number.isInteger(amount) ? amount : amount.toFixed(2)}`;
  try {
    return new Intl.NumberFormat(undefined, {
      style: "currency",
      currency,
      minimumFractionDigits: 2,
    }).format(amount);
  } catch (_) {
    return `${amount.toFixed(2)} ${currency ?? ""}`.trim();
  }
}

function showToast(message, type = "success") {
  const toast = el("toast");
  toast.textContent = message;
  toast.classList.toggle("error", type === "error");
  toast.classList.add("show");
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => toast.classList.remove("show"), 2600);
}

function renderNetworkState() {
  const offline = navigator.onLine === false;
  el("networkBanner").classList.toggle("hidden", !offline);
}

function setFatalError(title, message) {
  loadingView.classList.add("hidden");
  authenticatedApp.classList.add("hidden");
  bottomNav.classList.add("hidden");
  errorView.classList.remove("hidden");
  el("errorTitle").textContent = title;
  el("errorMessage").textContent = message;
  document.getElementById("app").setAttribute("aria-busy", "false");
}

async function api(path, options = {}) {
  const headers = new Headers(options.headers ?? {});
  if (options.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  if (state.token) headers.set("Authorization", `Bearer ${state.token}`);

  const response = await fetch(path, { ...options, headers });
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = body?.detail;
    throw new Error(typeof detail === "string" ? detail : `Request failed (${response.status}).`);
  }
  return body;
}

async function authenticate() {
  const params = new URLSearchParams(window.location.search);
  state.botId = params.get("bot_id");
  if (!state.botId) throw new Error("This Mini App URL is missing the required bot_id configuration.");
  if (!tg) throw new Error("Telegram WebApp API is unavailable. Open this page from the configured Telegram bot.");
  if (!tg.initData) throw new Error("Telegram did not provide signed initData. Reopen the Mini App from the bot.");

  const auth = await api("/api/v1/auth/telegram-miniapp", {
    method: "POST",
    body: JSON.stringify({ init_data: tg.initData, bot_id: state.botId }),
  });
  state.token = auth.access_token;
}

async function loadBootstrap() {
  state.bootstrap = await api("/api/v1/storefront/bootstrap");
  renderBootstrap();
}

function cacheCatalogVariants(products) {
  for (const product of products ?? []) {
    for (const variant of product.variants ?? []) {
      state.variantCache.set(variant.id, {
        ...variant,
        productTitle: product.title,
        deliveryEta: product.metadata?.delivery_eta ?? null,
      });
    }
  }
}

function renderCatalogLoading({ append = false } = {}) {
  if (append || state.catalog.products.length) return;
  productGrid.innerHTML = Array.from({ length: 4 }, () => `
    <article class="product-card skeleton" aria-hidden="true">
      <div class="skeleton-block skeleton-visual"></div>
      <div class="skeleton-block skeleton-line medium"></div>
      <div class="skeleton-block skeleton-line short"></div>
      <div class="skeleton-block skeleton-line medium"></div>
    </article>
  `).join("");
}

function catalogParams(offset = 0) {
  const params = new URLSearchParams({ offset: String(offset), limit: "12" });
  if (state.selectedCategory) params.set("category_id", state.selectedCategory);
  if (state.catalogQuery) params.set("q", state.catalogQuery);
  if (state.catalogAvailable !== null) params.set("available", String(state.catalogAvailable));
  return params;
}

async function loadCatalog(categoryId = state.selectedCategory, { append = false } = {}) {
  if (state.catalogBusy && append) return;
  if (categoryId !== state.selectedCategory) append = false;
  state.selectedCategory = categoryId;
  const offset = append ? (state.catalog.next_offset ?? state.catalog.products.length) : 0;
  const requestId = ++state.catalogRequestId;
  state.catalogBusy = true;
  el("catalogError").classList.add("hidden");
  loadMoreProductsButton.disabled = true;
  renderCatalogLoading({ append });
  try {
    const response = await api(`/api/v1/storefront/catalog?${catalogParams(offset)}`);
    if (requestId !== state.catalogRequestId) return;
    cacheCatalogVariants(response.products);
    state.catalog = {
      ...response,
      products: append ? [...state.catalog.products, ...response.products] : response.products,
    };
    renderCategories();
    renderProducts();
  } catch (error) {
    if (requestId !== state.catalogRequestId) return;
    renderProducts();
    el("catalogErrorMessage").textContent = navigator.onLine === false
      ? "You appear to be offline. Reconnect and retry."
      : (error.message || "Check your connection and try again.");
    el("catalogError").classList.remove("hidden");
    throw error;
  } finally {
    if (requestId === state.catalogRequestId) {
      state.catalogBusy = false;
      loadMoreProductsButton.disabled = false;
      renderCatalogMeta();
    }
  }
}

async function loadOrders() {
  state.orders = await api("/api/v1/storefront/orders?limit=30");
  renderOrders();
}

async function loadTopupOptions() {
  const response = await api("/api/v1/storefront/wallet/topups/options");
  state.topupOptions = response.providers ?? [];
  if (state.bootstrap) renderBootstrap();
}

function renderBootstrap() {
  const { store, user, wallets } = state.bootstrap;
  const firstName = user.first_name || user.username || "there";
  const fullName = [user.first_name, user.last_name].filter(Boolean).join(" ") || user.username || "Telegram User";
  const initial = fullName.trim().charAt(0).toUpperCase() || "U";

  document.title = store.name;
  el("storeName").textContent = store.name;
  const brandMark = el("brandMark");
  const logoUrl = safeHttpsUrl(store.settings?.brand_logo_url);
  brandMark.textContent = logoUrl ? "" : (store.name.trim().charAt(0).toUpperCase() || "G");
  brandMark.style.backgroundImage = logoUrl ? `url("${logoUrl.replaceAll('"', '%22')}")` : "";
  brandMark.classList.toggle("has-logo", Boolean(logoUrl));
  el("profileInitial").textContent = initial;
  el("heroGreeting").textContent = `Hi ${firstName}. Find your next purchase.`;
  el("storeTagline").textContent = store.settings?.store_tagline || store.settings?.store_description || "Fast checkout. Secure delivery. Built for Telegram.";
  el("accountAvatar").textContent = initial;
  el("accountName").textContent = fullName;
  el("accountUsername").textContent = user.username ? `@${user.username}` : "Secure Mini App session";

  const accent = store.settings?.brand_accent;
  if (/^#[0-9a-fA-F]{6}$/.test(accent ?? "")) document.documentElement.style.setProperty("--accent", accent);

  const walletGrid = el("walletGrid");
  walletGrid.innerHTML = wallets.map((wallet) => `
    <article class="wallet-card">
      <span class="wallet-currency">${escapeHtml(wallet.currency)} WALLET</span>
      <strong class="wallet-balance">${escapeHtml(money(wallet.balance, wallet.currency))}</strong>
      <div class="wallet-actions">
        <span class="field-help">Provider-confirmed balance</span>
        ${state.topupOptions.length ? `<button class="wallet-fund-button" data-fund-currency="${escapeHtml(wallet.currency)}" type="button">Add funds</button>` : ""}
      </div>
    </article>
  `).join("");
  el("emptyWallets").classList.toggle("hidden", wallets.length > 0);
  el("fundWalletButton").classList.toggle("hidden", state.topupOptions.length === 0);
}

function renderCategories() {
  const categories = state.catalog.categories ?? [];
  const buttons = [{ id: null, name: "All" }, ...categories.map((category) => ({ id: category.id, name: category.name }))];
  categoryRail.innerHTML = buttons.map((category) => `
    <button class="category-pill ${state.selectedCategory === category.id ? "active" : ""}" data-category-id="${category.id ?? ""}" type="button">
      ${escapeHtml(category.name)}
    </button>
  `).join("");
}

function renderCatalogMeta() {
  const loaded = state.catalog.products?.length ?? 0;
  const total = state.catalog.total ?? loaded;
  const suffix = state.catalogQuery ? ` for “${state.catalogQuery}”` : "";
  el("catalogMeta").textContent = total
    ? `${loaded} of ${total} product${total === 1 ? "" : "s"}${suffix}`
    : (state.catalogBusy ? "Loading products…" : `0 products${suffix}`);
  loadMoreProductsButton.classList.toggle("hidden", !state.catalog.has_more || loaded === 0);
  loadMoreProductsButton.disabled = state.catalogBusy;
}

function renderProducts() {
  const products = state.catalog.products ?? [];
  productGrid.innerHTML = products.map((product) => {
    const imageUrl = safeImageUrl(product.metadata?.image_url || product.metadata?.thumbnail_url);
    const badge = product.metadata?.badge || (product.metadata?.featured ? "FEATURED" : "");
    const deliveryEta = product.metadata?.delivery_eta;
    const visual = imageUrl
      ? `<img src="${escapeHtml(imageUrl)}" alt="" loading="lazy" referrerpolicy="no-referrer">`
      : `<div class="product-placeholder" aria-hidden="true"></div>`;
    const variants = product.variants.map((variant) => {
      const inStock = Number(variant.stock_quantity) > 0;
      const stockLabel = inStock
        ? `${variant.stock_quantity} available`
        : "Sold out";
      return `
        <div class="variant-row">
          <div class="variant-info">
            <span class="variant-title">${escapeHtml(variant.title)}</span>
            <strong class="variant-price">${escapeHtml(money(variant.price, variant.currency))}</strong>
            <span class="variant-meta"><span class="stock-dot ${inStock ? "" : "sold-out"}"></span>${escapeHtml(stockLabel)} · ${escapeHtml(variant.sku)}</span>
          </div>
          <button class="add-button" data-add-variant="${variant.id}" type="button" ${inStock ? "" : "disabled"} aria-label="${inStock ? "Add" : "Sold out:"} ${escapeHtml(product.title)} ${escapeHtml(variant.title)}">${inStock ? "+" : "×"}</button>
        </div>
      `;
    }).join("");
    return `
      <article class="product-card">
        <div class="product-visual">
          ${visual}
          ${badge ? `<span class="product-badge">${escapeHtml(badge)}</span>` : ""}
        </div>
        <div class="product-body">
          <h3 class="product-title">${escapeHtml(product.title)}</h3>
          <p class="product-description">${escapeHtml(product.description || "Ready for instant checkout.")}</p>
          ${deliveryEta ? `<span class="delivery-chip">Delivery · ${escapeHtml(deliveryEta)}</span>` : ""}
          <div class="variant-list">${variants}</div>
        </div>
      </article>
    `;
  }).join("");

  const empty = !state.catalogBusy && products.length === 0;
  el("emptyCatalog").classList.toggle("hidden", !empty);
  if (empty) {
    el("emptyCatalogTitle").textContent = state.catalogQuery ? "No matching products" : "No products available";
    el("emptyCatalogMessage").textContent = state.catalogQuery
      ? "Try a different search phrase, category, or stock filter."
      : "This store has no active products for the selected filters.";
  }
  renderCatalogMeta();
}

function orderStatusClass(status) {
  if (["FULFILLED", "PAID"].includes(status)) return "success";
  if (["FAILED", "REFUNDED", "CANCELLED"].includes(status)) return "danger";
  return "warning";
}

function renderOrders() {
  const orders = state.orders ?? [];
  el("ordersList").innerHTML = orders.map((order) => {
    const date = new Date(order.created_at);
    const label = Number.isNaN(date.getTime()) ? "" : new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(date);
    const units = order.items.reduce((sum, item) => sum + item.quantity, 0);
    return `
      <article class="order-card">
        <div class="order-top">
          <div><span class="order-number">${escapeHtml(order.order_number)}</span><span class="order-date">${escapeHtml(label)}</span></div>
          <span class="status-chip ${orderStatusClass(order.status)}">${escapeHtml(order.status)}</span>
        </div>
        <div class="order-divider"></div>
        <div class="order-bottom">
          <span class="order-items-count">${units} item${units === 1 ? "" : "s"}</span>
          <strong class="order-total">${escapeHtml(money(order.total_amount, order.currency))}</strong>
        </div>
      </article>
    `;
  }).join("");
  el("emptyOrders").classList.toggle("hidden", orders.length > 0);
}

function findVariant(variantId) {
  return state.variantCache.get(variantId) ?? null;
}

function addToCart(variantId) {
  const variant = findVariant(variantId);
  if (!variant) return;
  if (Number(variant.stock_quantity) <= 0) {
    showToast("This option is currently sold out.", "error");
    return;
  }
  const existing = state.cart.get(variantId);
  const maxQuantity = Math.min(100, Number(variant.stock_quantity));
  state.cart.set(variantId, { variant, quantity: Math.min((existing?.quantity ?? 0) + 1, maxQuantity) });
  state.checkoutKey = null;
  renderCart();
  haptic("light");
  showToast(`${variant.productTitle} added to cart.`);
}

function changeQuantity(variantId, delta) {
  const item = state.cart.get(variantId);
  if (!item) return;
  const next = item.quantity + delta;
  if (next <= 0) state.cart.delete(variantId);
  else {
    const maxQuantity = Math.min(100, Number(item.variant.stock_quantity));
    state.cart.set(variantId, { ...item, quantity: Math.min(next, maxQuantity) });
  }
  state.checkoutKey = null;
  renderCart();
  haptic("light");
}

function cartTotals() {
  let units = 0;
  let amount = 0;
  let currency = null;
  let mixedCurrency = false;
  for (const { variant, quantity } of state.cart.values()) {
    units += quantity;
    amount += Number(variant.price) * quantity;
    if (currency && currency !== variant.currency) mixedCurrency = true;
    currency = currency || variant.currency;
  }
  return { units, amount, currency, mixedCurrency };
}

function renderCart() {
  const entries = [...state.cart.entries()];
  el("cartItems").innerHTML = entries.map(([variantId, item]) => `
    <div class="cart-item">
      <div>
        <div class="cart-item-title">${escapeHtml(item.variant.productTitle)}</div>
        <div class="cart-item-sub">${escapeHtml(item.variant.title)} · ${escapeHtml(money(item.variant.price, item.variant.currency))}</div>
      </div>
      <div class="quantity-control">
        <button class="quantity-button" data-quantity="-1" data-variant-id="${variantId}" type="button" aria-label="Decrease quantity">−</button>
        <span class="quantity-value">${item.quantity}</span>
        <button class="quantity-button" data-quantity="1" data-variant-id="${variantId}" type="button" aria-label="Increase quantity">+</button>
      </div>
    </div>
  `).join("");

  const totals = cartTotals();
  el("cartTotal").textContent = totals.mixedCurrency ? "Multiple currencies" : money(totals.amount, totals.currency || "USD");
  el("checkoutButton").disabled = entries.length === 0 || state.checkoutBusy || totals.mixedCurrency;

  if (tg?.MainButton) {
    if (totals.units > 0) {
      tg.MainButton.setParams({ text: `Cart · ${totals.units}`, is_visible: true, is_active: true });
    } else {
      tg.MainButton.hide();
    }
  }
}

function openCart() {
  if (!state.cart.size) return;
  cartBackdrop.classList.remove("hidden");
  cartSheet.classList.add("open");
  cartSheet.setAttribute("aria-hidden", "false");
  if (tg?.BackButton) tg.BackButton.show();
}

function closeCart() {
  cartSheet.classList.remove("open");
  cartSheet.setAttribute("aria-hidden", "true");
  window.setTimeout(() => cartBackdrop.classList.add("hidden"), 260);
  if (tg?.BackButton) tg.BackButton.hide();
}

async function checkout() {
  if (state.checkoutBusy || !state.cart.size) return;
  const recipient = recipientInput.value.trim();
  if (!recipient) {
    showToast("Enter the delivery recipient before checkout.", "error");
    recipientInput.focus();
    notifyHaptic("error");
    return;
  }
  const totals = cartTotals();
  if (totals.mixedCurrency) {
    showToast("Checkout supports one currency at a time.", "error");
    return;
  }

  state.checkoutKey = state.checkoutKey || (crypto.randomUUID?.() ?? `webapp-${Date.now()}-${Math.random().toString(16).slice(2)}`);
  const payload = {
    items: [...state.cart.values()].map(({ variant, quantity }) => ({ variant_id: variant.id, quantity })),
    recipient,
    idempotency_key: state.checkoutKey,
  };

  state.checkoutBusy = true;
  renderCart();
  el("checkoutButton").textContent = "Processing…";
  try {
    tg?.MainButton?.showProgress?.();
    const order = await api("/api/v1/storefront/checkout", { method: "POST", body: JSON.stringify(payload) });
    state.cart.clear();
    state.checkoutKey = null;
    recipientInput.value = "";
    closeCart();
    notifyHaptic("success");
    showToast(`Order ${order.order_number} confirmed.`);
    await Promise.all([loadBootstrap(), loadOrders(), loadCatalog(state.selectedCategory)]);
    switchView("orders");
  } catch (error) {
    notifyHaptic("error");
    showToast(error.message || "Checkout failed.", "error");
  } finally {
    state.checkoutBusy = false;
    tg?.MainButton?.hideProgress?.();
    el("checkoutButton").textContent = "Pay from wallet";
    renderCart();
  }
}

function topupStorageKey() {
  const tenantId = state.bootstrap?.store?.id;
  const userId = state.bootstrap?.user?.id;
  return tenantId && userId ? `ghbf.topup.${tenantId}.${userId}` : null;
}

function persistActiveTopup() {
  const key = topupStorageKey();
  if (!key) return;
  if (state.activeTopup?.id && !["SUCCEEDED", "FAILED", "EXPIRED", "CANCELLED"].includes(state.activeTopup.status)) {
    localStorage.setItem(key, state.activeTopup.id);
  } else {
    localStorage.removeItem(key);
  }
}

function selectedTopupProvider() {
  return state.topupOptions.find((provider) => provider.provider_name === topupProviderSelect.value) ?? null;
}

function renderTopupForm(preferredCurrency = null) {
  const providers = state.topupOptions ?? [];
  if (!providers.length) {
    topupProviderSelect.innerHTML = "";
    topupCurrencySelect.innerHTML = "";
    el("topupPolicyHelp").textContent = "No wallet funding provider is currently available.";
    el("topupSubmitButton").disabled = true;
    return;
  }

  const currentProvider = providers.find((provider) => provider.provider_name === topupProviderSelect.value) ?? providers[0];
  topupProviderSelect.innerHTML = providers.map((provider) => `
    <option value="${escapeHtml(provider.provider_name)}" ${provider.provider_name === currentProvider.provider_name ? "selected" : ""}>${escapeHtml(provider.display_name)}</option>
  `).join("");

  const currencies = currentProvider.currencies ?? [];
  const selectedCurrency = currencies.includes(preferredCurrency)
    ? preferredCurrency
    : (currencies.includes(topupCurrencySelect.value) ? topupCurrencySelect.value : currencies[0]);
  topupCurrencySelect.innerHTML = currencies.map((currency) => `
    <option value="${escapeHtml(currency)}" ${currency === selectedCurrency ? "selected" : ""}>${escapeHtml(currency)}</option>
  `).join("");

  topupAmountInput.min = currentProvider.min_amount;
  topupAmountInput.max = currentProvider.max_amount;
  topupAmountInput.step = currentProvider.whole_units_only ? "1" : "0.01";
  el("topupPolicyHelp").textContent = `Allowed: ${money(currentProvider.min_amount, selectedCurrency)} – ${money(currentProvider.max_amount, selectedCurrency)}.`;
  const termsRow = el("topupTermsRow");
  const termsLink = el("topupTermsLink");
  const termsCheckbox = el("topupTermsCheckbox");
  termsRow.classList.toggle("hidden", !currentProvider.terms_required);
  termsCheckbox.required = Boolean(currentProvider.terms_required);
  if (currentProvider.terms_url) termsLink.href = currentProvider.terms_url;
  else termsLink.removeAttribute("href");
  el("topupSubmitButton").disabled = state.topupBusy;
}

function topupStatusClass(status) {
  if (status === "SUCCEEDED") return "success";
  if (["FAILED", "EXPIRED", "CANCELLED"].includes(status)) return "danger";
  return "warning";
}

function renderTopupStatus() {
  const topup = state.activeTopup;
  const form = el("topupForm");
  const card = el("topupStatusCard");
  if (!topup) {
    form.classList.remove("hidden");
    card.classList.add("hidden");
    return;
  }

  form.classList.add("hidden");
  card.classList.remove("hidden");
  const label = el("topupStatusLabel");
  label.textContent = topup.status;
  label.className = `status-chip ${topupStatusClass(topup.status)}`;
  el("topupStatusAmount").textContent = money(topup.amount, topup.currency);
  const messages = {
    SUCCEEDED: `Funds confirmed. Wallet balance: ${money(topup.wallet_balance, topup.currency)}.`,
    FAILED: "The payment provider reported that this payment failed.",
    EXPIRED: "This payment session expired. Start a new top-up to continue.",
    CANCELLED: "This payment was cancelled.",
    UNKNOWN: "Provider status is temporarily uncertain. We will keep checking safely.",
    PROCESSING: "The provider is processing your payment.",
    PENDING: "Waiting for the payment provider to confirm settlement.",
    CREATED: "Payment session created. Continue to the provider to complete it.",
  };
  el("topupStatusMessage").textContent = messages[topup.status] || "Waiting for payment confirmation.";

  const checkoutUrl = safeHttpsUrl(topup.checkout_url);
  el("topupOpenCheckoutButton").classList.toggle("hidden", !checkoutUrl || topup.status === "SUCCEEDED");
  el("topupCheckButton").classList.toggle("hidden", ["SUCCEEDED", "FAILED", "EXPIRED", "CANCELLED"].includes(topup.status));
}

function openPaymentCheckout() {
  const checkoutUrl = safeHttpsUrl(state.activeTopup?.checkout_url);
  if (!checkoutUrl) {
    showToast("Payment provider did not supply a valid checkout URL.", "error");
    return;
  }
  const isStars = state.activeTopup?.provider === "telegram_stars";
  if (isStars && tg?.openInvoice) {
    try {
      tg.openInvoice(checkoutUrl, (invoiceStatus) => {
        if (invoiceStatus === "paid") {
          window.setTimeout(() => reconcileActiveTopup({ quiet: true }), 350);
        } else if (invoiceStatus === "failed") {
          showToast("Telegram could not complete the Stars payment.", "error");
        }
      });
      return;
    } catch (_) {
      showToast("Could not open the Telegram Stars invoice.", "error");
      return;
    }
  }
  try {
    if (tg?.openLink) tg.openLink(checkoutUrl);
    else window.open(checkoutUrl, "_blank", "noopener,noreferrer");
  } catch (_) {
    window.open(checkoutUrl, "_blank", "noopener,noreferrer");
  }
}


function openTopup(preferredCurrency = null) {
  if (state.activeTopup && ["SUCCEEDED", "FAILED", "EXPIRED", "CANCELLED"].includes(state.activeTopup.status) && !topupSheet.classList.contains("open")) {
    resetTopup();
  }
  if (!state.topupOptions.length) {
    showToast("Wallet funding is not configured for this store.", "error");
    return;
  }
  closeCart();
  topupBackdrop.classList.remove("hidden");
  topupSheet.classList.add("open");
  topupSheet.setAttribute("aria-hidden", "false");
  if (!state.activeTopup) {
    renderTopupForm(preferredCurrency);
    renderTopupStatus();
  } else {
    renderTopupStatus();
  }
  tg?.BackButton?.show?.();
}

function closeTopup() {
  topupSheet.classList.remove("open");
  topupSheet.setAttribute("aria-hidden", "true");
  window.setTimeout(() => topupBackdrop.classList.add("hidden"), 260);
  if (!cartSheet.classList.contains("open")) tg?.BackButton?.hide?.();
}

function stopTopupPolling() {
  if (state.topupPollTimer) window.clearInterval(state.topupPollTimer);
  state.topupPollTimer = null;
}

async function reconcileActiveTopup({ quiet = false } = {}) {
  if (!state.activeTopup?.id || state.topupBusy) return;
  state.topupBusy = true;
  el("topupCheckButton").disabled = true;
  try {
    state.activeTopup = await api(`/api/v1/storefront/wallet/topups/${state.activeTopup.id}/reconcile`, { method: "POST" });
    renderTopupStatus();
    persistActiveTopup();
    if (state.activeTopup.status === "SUCCEEDED") {
      stopTopupPolling();
      await loadBootstrap();
      notifyHaptic("success");
      showToast(`Wallet funded with ${money(state.activeTopup.amount, state.activeTopup.currency)}.`);
    } else if (["FAILED", "EXPIRED", "CANCELLED"].includes(state.activeTopup.status)) {
      stopTopupPolling();
      if (!quiet) showToast("Payment did not complete.", "error");
    }
  } catch (error) {
    if (!quiet) showToast(error.message || "Could not check payment status.", "error");
  } finally {
    state.topupBusy = false;
    el("topupCheckButton").disabled = false;
  }
}

function startTopupPolling() {
  stopTopupPolling();
  if (!state.activeTopup?.id || ["SUCCEEDED", "FAILED", "EXPIRED", "CANCELLED"].includes(state.activeTopup.status)) return;
  state.topupPollTimer = window.setInterval(() => {
    if (document.visibilityState === "visible") reconcileActiveTopup({ quiet: true });
  }, 4000);
}

async function createTopup() {
  if (state.topupBusy) return;
  const provider = selectedTopupProvider();
  const currency = topupCurrencySelect.value;
  const rawAmount = topupAmountInput.value.trim();
  const amount = Number(rawAmount);
  if (!provider || !currency || !/^\d+(?:\.\d{1,2})?$/.test(rawAmount) || !Number.isFinite(amount)) {
    showToast("Choose a provider, currency, and valid amount.", "error");
    return;
  }
  const min = Number(provider.min_amount);
  const max = Number(provider.max_amount);
  if (provider.whole_units_only && !Number.isInteger(amount)) {
    showToast("This payment method requires a whole-number amount.", "error");
    return;
  }
  if (provider.terms_required && !el("topupTermsCheckbox").checked) {
    showToast("Accept the payment terms before continuing.", "error");
    return;
  }
  if (amount < min || amount > max) {
    showToast(`Amount must be between ${money(min, currency)} and ${money(max, currency)}.`, "error");
    return;
  }

  state.topupKey = state.topupKey || (crypto.randomUUID?.() ?? `topup-${Date.now()}-${Math.random().toString(16).slice(2)}`);
  state.topupBusy = true;
  el("topupSubmitButton").disabled = true;
  el("topupSubmitButton").textContent = "Creating payment…";
  try {
    state.activeTopup = await api("/api/v1/storefront/wallet/topups", {
      method: "POST",
      body: JSON.stringify({
        amount: amount.toFixed(2),
        currency,
        provider_name: provider.provider_name,
        idempotency_key: state.topupKey,
        terms_accepted: Boolean(el("topupTermsCheckbox").checked),
      }),
    });
    persistActiveTopup();
    renderTopupStatus();
    haptic("medium");
    if (safeHttpsUrl(state.activeTopup.checkout_url)) openPaymentCheckout();
    startTopupPolling();
  } catch (error) {
    notifyHaptic("error");
    showToast(error.message || "Could not create wallet top-up.", "error");
  } finally {
    state.topupBusy = false;
    el("topupSubmitButton").disabled = false;
    el("topupSubmitButton").textContent = "Continue to payment";
  }
}

async function restorePendingTopup() {
  const key = topupStorageKey();
  const intentId = key ? localStorage.getItem(key) : null;
  if (!intentId) return;
  try {
    state.activeTopup = await api(`/api/v1/storefront/wallet/topups/${intentId}`);
    persistActiveTopup();
    if (!["SUCCEEDED", "FAILED", "EXPIRED", "CANCELLED"].includes(state.activeTopup.status)) {
      startTopupPolling();
    }
  } catch (_) {
    localStorage.removeItem(key);
    state.activeTopup = null;
  }
}

function resetTopup() {
  stopTopupPolling();
  state.activeTopup = null;
  state.topupKey = null;
  persistActiveTopup();
  topupAmountInput.value = "";
  el("topupTermsCheckbox").checked = false;
  renderTopupForm();
  renderTopupStatus();
}

function handleBackButton() {
  if (topupSheet.classList.contains("open")) closeTopup();
  else if (cartSheet.classList.contains("open")) closeCart();
}

function switchView(target) {
  document.querySelectorAll(".view").forEach((view) => view.classList.toggle("active", view.dataset.view === target));
  document.querySelectorAll(".nav-button").forEach((button) => button.classList.toggle("active", button.dataset.target === target));
  if (target === "orders") loadOrders().catch((error) => showToast(error.message, "error"));
  window.scrollTo({ top: 0, behavior: "smooth" });
  haptic("light");
}

function bindEvents() {
  document.addEventListener("click", async (event) => {
    const addButton = event.target.closest("[data-add-variant]");
    if (addButton) return addToCart(addButton.dataset.addVariant);

    const quantityButton = event.target.closest("[data-quantity]");
    if (quantityButton) return changeQuantity(quantityButton.dataset.variantId, Number(quantityButton.dataset.quantity));

    const categoryButton = event.target.closest("[data-category-id]");
    if (categoryButton) {
      const id = categoryButton.dataset.categoryId || null;
      try { await loadCatalog(id); haptic("light"); } catch (error) { showToast(error.message, "error"); }
      return;
    }

    const fundButton = event.target.closest("[data-fund-currency]");
    if (fundButton) return openTopup(fundButton.dataset.fundCurrency || null);

    const quickAmountButton = event.target.closest("[data-topup-amount]");
    if (quickAmountButton) {
      topupAmountInput.value = quickAmountButton.dataset.topupAmount;
      state.topupKey = null;
      haptic("light");
      return;
    }

    const navButton = event.target.closest(".nav-button[data-target]");
    if (navButton) switchView(navButton.dataset.target);
  });

  el("profileButton").addEventListener("click", () => switchView("account"));
  el("refreshCatalogButton").addEventListener("click", () => loadCatalog(state.selectedCategory).catch((error) => showToast(error.message, "error")));
  catalogSearchInput.addEventListener("input", () => {
    window.clearTimeout(bindEvents.catalogSearchTimer);
    bindEvents.catalogSearchTimer = window.setTimeout(() => {
      state.catalogQuery = catalogSearchInput.value.trim();
      loadCatalog(state.selectedCategory).catch((error) => showToast(error.message, "error"));
    }, 320);
  });
  catalogAvailabilitySelect.addEventListener("change", () => {
    const value = catalogAvailabilitySelect.value;
    state.catalogAvailable = value === "available" ? true : (value === "soldout" ? false : null);
    loadCatalog(state.selectedCategory).catch((error) => showToast(error.message, "error"));
  });
  loadMoreProductsButton.addEventListener("click", () => {
    loadCatalog(state.selectedCategory, { append: true }).catch((error) => showToast(error.message, "error"));
  });
  el("catalogRetryButton").addEventListener("click", () => {
    loadCatalog(state.selectedCategory).catch((error) => showToast(error.message, "error"));
  });
  window.addEventListener("online", () => { renderNetworkState(); loadCatalog(state.selectedCategory).catch(() => {}); });
  window.addEventListener("offline", renderNetworkState);
  renderNetworkState();
  el("refreshOrdersButton").addEventListener("click", () => loadOrders().catch((error) => showToast(error.message, "error")));
  el("closeCartButton").addEventListener("click", closeCart);
  cartBackdrop.addEventListener("click", closeCart);
  el("checkoutButton").addEventListener("click", checkout);
  recipientInput.addEventListener("input", () => { state.checkoutKey = null; });
  el("retryButton").addEventListener("click", () => window.location.reload());

  el("fundWalletButton").addEventListener("click", () => openTopup());
  el("closeTopupButton").addEventListener("click", closeTopup);
  topupBackdrop.addEventListener("click", closeTopup);
  el("topupSubmitButton").addEventListener("click", createTopup);
  el("topupCheckButton").addEventListener("click", () => reconcileActiveTopup());
  el("topupOpenCheckoutButton").addEventListener("click", openPaymentCheckout);
  topupProviderSelect.addEventListener("change", () => { state.topupKey = null; renderTopupForm(); });
  topupCurrencySelect.addEventListener("change", () => { state.topupKey = null; renderTopupForm(topupCurrencySelect.value); });
  topupAmountInput.addEventListener("input", () => { state.topupKey = null; });
  el("topupTermsCheckbox").addEventListener("change", () => { state.topupKey = null; });
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible" && state.activeTopup) reconcileActiveTopup({ quiet: true });
  });

  tg?.MainButton?.onClick?.(openCart);
  tg?.BackButton?.onClick?.(handleBackButton);
}

async function boot() {
  try {
    tg?.ready?.();
    tg?.expand?.();
    bindEvents();
    await authenticate();
    await Promise.all([loadBootstrap(), loadCatalog(), loadOrders(), loadTopupOptions()]);
    renderBootstrap();
    await restorePendingTopup();
    loadingView.classList.add("hidden");
    errorView.classList.add("hidden");
    authenticatedApp.classList.remove("hidden");
    bottomNav.classList.remove("hidden");
    document.getElementById("app").setAttribute("aria-busy", "false");
    renderCart();
  } catch (error) {
    console.error("Mini App bootstrap failed", error);
    setFatalError("Unable to open the store", error.message || "The storefront could not be initialized.");
  }
}

boot();
