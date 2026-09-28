import {pendingOperations} from "./pending-operations.js?v=20260922_06";
import {previewMode,previewAPI,bindPreview} from "./preview.js?v=20260922_06";
import {bindExperience,bindSheetAccess} from "./experience.js?v=20260922_06";
import {bindCustomerCare} from "./customer-care.js?v=20260922_06";
import {t, format, setLanguage, localizeStatic} from "./locale.js?v=20260922_06";
let experience=null,customerCare=null,pending=null;
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
  ordersRevision: 0,
  ordersMore: false,
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
    return url.protocol === "https:" && !url.username && !url.password ? url.href : null;
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
    return new Intl.NumberFormat(document.documentElement.lang, {
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
  document.documentElement.classList.remove("booting");
  el("errorTitle").textContent = title;
  el("errorMessage").textContent = message;
  document.getElementById("app").setAttribute("aria-busy", "false");
}

async function api(path, options = {}) {
  if(previewMode)return previewAPI(path,options);
  const headers = new Headers(options.headers ?? {});
  if (options.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  if (state.token) headers.set("Authorization", `Bearer ${state.token}`);

  const response = await fetch(path, { ...options, headers });
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = body?.detail;
    const error = new Error(typeof detail === "string" ? detail : `Request failed (${response.status}).`);
    error.status=response.status;
    throw error;
  }
  return body;
}

async function authenticate() {
  if(previewMode){bindPreview();return;}
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
      ? t("You appear to be offline. Reconnect and retry.")
      : (error.message || t("Check your connection and try again."));
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

async function loadOrders(append=false) {
  const revision=++state.ordersRevision;
  el("ordersMore").disabled=true;
  try {
    const rows=await api(`/api/v1/storefront/orders?limit=30&offset=${append?state.orders.length:0}`);
    if(revision!==state.ordersRevision)return;
    state.orders=append?[...state.orders,...rows.filter(row=>!state.orders.some(previous=>previous.id===row.id))]:rows;
    state.ordersMore=rows.length===30;
    el("ordersMore").classList.toggle("hidden",!state.ordersMore);
    renderOrders();
  }finally{if(revision===state.ordersRevision)el("ordersMore").disabled=false;}
}

async function loadTopupOptions() {
  const [response, methods] = await Promise.all([
    api("/api/v1/storefront/wallet/topups/options"),
    api("/api/v1/storefront/wallet/payment-methods"),
  ]);
  state.topupOptions = [
    ...(methods.methods ?? []).map(method => ({ ...method, provider_name: method.id })),
    ...(response.providers ?? []),
  ];
  if (state.bootstrap) renderBootstrap();
}

function renderBootstrap() {
  const { store, user, wallets } = state.bootstrap;
  StoreThemes.ready.then(catalog=>StoreThemes.apply(document.documentElement,catalog,store.settings?.miniapp_theme,store.settings?.brand_accent)).catch(()=>{});
  setLanguage(store.settings?.locale || "en", store.id);
  pending?.render();
  el("customerIdentity").textContent = `${t("Account ID")}: ${user.id}`;
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
  el("heroGreeting").textContent = t("greeting").replace("{name}", firstName);
  el("storeTagline").textContent = store.settings?.store_tagline || store.settings?.store_description || t("Fast checkout. Secure delivery. Built for Telegram.");
  el("accountAvatar").textContent = initial;
  el("accountName").textContent = fullName;
  el("accountUsername").textContent = user.username ? `@${user.username}` : t("Secure Mini App session");

  const bType = store.business_type || "GENERAL";
  const tKey = store.template_key || "general-commerce";
  document.body.dataset.businessType = bType;
  document.body.dataset.templateKey = tKey;

  const verticalLabels = {
    NUMBER_SMS: { search: "Search services (Telegram, WhatsApp, Google…)" },
    ACCOUNT: { search: "Search accounts, platforms, regions…" },
    GIFT_CARD: { search: "Search gift cards, games, vouchers…" },
    DIGITAL_PRODUCT: { search: "Search digital products, keys, licenses…" },
    RESELLER: { search: "Search products across suppliers…" },
    HYBRID: { search: "Search catalog…" },
  };
  const vertical = verticalLabels[bType] || { search: t("Search products, plans, or SKU") };
  if (catalogSearchInput && vertical.search) {
    catalogSearchInput.placeholder = t(vertical.search);
  }

  const enabledModules = Array.isArray(store.enabled_modules) ? store.enabled_modules : ["catalog", "orders", "account"];
  const ordersNav = document.querySelector('.nav-button[data-target="orders"]');
  if (ordersNav) ordersNav.classList.toggle("hidden", !enabledModules.includes("orders"));
  const walletNav = document.querySelector('.nav-button[data-target="wallet"]');
  if (walletNav) walletNav.classList.toggle("hidden", !enabledModules.includes("account"));
  const settingsNav = document.querySelector('.nav-button[data-target="settings"], .nav-button[data-target="account"]');
  if (settingsNav) settingsNav.classList.toggle("hidden", !enabledModules.includes("account"));

  const accent = store.settings?.brand_accent;
  if (/^#[0-9a-fA-F]{6}$/.test(accent ?? "")) document.documentElement.style.setProperty("--accent", accent);

  function getProviderMeta(provider) {
    const pName = (provider.provider_name || "").toLowerCase();
    const displayName = provider.name || provider.display_name || provider.provider_name;
    if (pName.includes("shamcash")) {
      return {
        icon: "🇸🇾",
        badge: "SYP",
        title: displayName || "ShamCash",
        subtitle: t("Syria ShamCash wallet transfer"),
      };
    }
    if (pName.includes("syriatel")) {
      return {
        icon: "📱",
        badge: "SYP",
        title: displayName || "Syriatel Cash",
        subtitle: t("Syriatel Cash mobile transfer"),
      };
    }
    if (pName.includes("crypto") || pName.includes("nowpayments") || pName.includes("gozapay")) {
      return {
        icon: "💎",
        badge: "USDT",
        title: displayName || "Crypto Pay",
        subtitle: t("Instant crypto settlement"),
      };
    }
    if (pName.includes("binance")) {
      return {
        icon: "🟡",
        badge: "BINANCE",
        title: displayName || "Binance Pay",
        subtitle: t("Zero-fee instant transfer"),
      };
    }
    if (pName.includes("bybit")) {
      return {
        icon: "🟠",
        badge: "BYBIT",
        title: displayName || "Bybit Pay",
        subtitle: t("Bybit wallet transfer"),
      };
    }
    if (pName.includes("stripe") || pName.includes("card")) {
      return {
        icon: "💳",
        badge: "CARD",
        title: displayName || "Bank Card",
        subtitle: t("Visa / Mastercard"),
      };
    }
    return {
      icon: "⚡",
      badge: (provider.currencies && provider.currencies[0]) || "FAST",
      title: displayName,
      subtitle: `${t("Top up via")} ${displayName}`,
    };
  }

  const rechargeGrid = el("rechargeMethodsGrid");
  if (rechargeGrid) {
    rechargeGrid.innerHTML = state.topupOptions.map((provider) => {
      const meta = getProviderMeta(provider);
      return `
        <article class="recharge-method-card" data-provider="${escapeHtml(provider.provider_name)}" role="button" tabindex="0" aria-label="${escapeHtml(meta.title)}">
          <div class="recharge-method-left">
            <div class="recharge-method-avatar" aria-hidden="true">${meta.icon}</div>
            <div class="recharge-method-info">
              <div class="recharge-method-name-row">
                <span class="recharge-method-title">${escapeHtml(meta.title)}</span>
                <span class="recharge-method-badge">${escapeHtml(meta.badge)}</span>
              </div>
              <span class="recharge-method-sub">${escapeHtml(meta.subtitle)}</span>
            </div>
          </div>
          <span class="recharge-method-cta">${t("Select")}</span>
        </article>
      `;
    }).join("");
    rechargeGrid.querySelectorAll('.recharge-method-card').forEach(card => {
      card.onclick = () => openTopup(null, false, card.dataset.provider);
      card.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") openTopup(null, false, card.dataset.provider); };
    });
  }
  experience?.renderHelp();
  const primaryWallet = wallets.find(w => w.currency === "USD") || wallets[0];
  const heroBal = primaryWallet ? escapeHtml(money(primaryWallet.balance, primaryWallet.currency)) : "$0.00";
  const heroEl = el("walletHeroBalance");
  if (heroEl) heroEl.textContent = heroBal;
  el("shopRecharge").classList.toggle("hidden",state.topupOptions.length===0);
  el("emptyRecharge")?.classList.toggle("hidden", state.topupOptions.length > 0);
  el("fundWalletButton")?.classList.toggle("hidden", state.topupOptions.length === 0);
  el("walletRechargeButton")?.classList.toggle("hidden", state.topupOptions.length === 0);
}

function renderCategories() {
  const categories = state.catalog.categories ?? [];
  const buttons = [{ id: null, name: t("All") }, ...categories.map((category) => ({ id: category.id, name: category.name }))];
  categoryRail.innerHTML = buttons.map((category) => `
    <button class="category-pill ${state.selectedCategory === category.id ? "active" : ""}" data-category-id="${category.id ?? ""}" type="button">
      ${escapeHtml(category.name)}
    </button>
  `).join("");
}

function renderCatalogMeta() {
  const loaded = state.catalog.products?.length ?? 0;
  const total = state.catalog.total ?? loaded;
  const suffix = state.catalogQuery ? ` · “${state.catalogQuery}”` : "";
  el("catalogMeta").textContent = total
    ? format("products_count",{loaded,total})+suffix
    : (state.catalogBusy ? t("Loading products…") : format("products_count",{loaded:0,total:0})+suffix);
  loadMoreProductsButton.classList.toggle("hidden", !state.catalog.has_more || loaded === 0);
  loadMoreProductsButton.disabled = state.catalogBusy;
}

function getProductIcon(title) {
  const lower = (title || "").toLowerCase();
  if (lower.includes("pubg") || lower.includes("free fire") || lower.includes("game") || lower.includes("razer") || lower.includes("steam") || lower.includes("uc ") || lower.includes("coins")) return "🎮";
  if (lower.includes("chatgpt") || lower.includes("gemini") || lower.includes("claude") || lower.includes("ai ") || lower.includes("bot") || lower.includes("duolingo")) return "🤖";
  if (lower.includes("itunes") || lower.includes("card") || lower.includes("gift") || lower.includes("voucher") || lower.includes("apple")) return "💳";
  if (lower.includes("vpn") || lower.includes("proxy") || lower.includes("account") || lower.includes("sub") || lower.includes("plus") || lower.includes("premium")) return "💎";
  if (lower.includes("sms") || lower.includes("number") || lower.includes("sim") || lower.includes("phone")) return "📱";
  return "⚡";
}

function renderProducts() {
  const products = state.catalog.products ?? [];
  productGrid.innerHTML = products.map((product) => {
    const imageUrl = safeImageUrl(product.metadata?.image_url || product.metadata?.thumbnail_url);
    const badge = product.metadata?.badge || (product.metadata?.featured ? t("FEATURED") : "");
    const deliveryEta = product.metadata?.delivery_eta;

    let minPrice = null;
    let minCurrency = "USD";
    let totalStock = 0;
    for (const v of product.variants ?? []) {
      const p = Number(v.price);
      if (minPrice === null || p < minPrice) {
        minPrice = p;
        minCurrency = v.currency;
      }
      totalStock += Number(v.stock_quantity ?? 0);
    }
    const hasMultiple = (product.variants?.length ?? 0) > 1;
    const isOutOfStock = totalStock <= 0;
    const priceText = minPrice !== null ? money(minPrice, minCurrency) : "—";

    const icon = getProductIcon(product.title);
    const initial = (product.title || "").charAt(0).toUpperCase();

    const visual = imageUrl
      ? `<img src="${escapeHtml(imageUrl)}" alt="${escapeHtml(product.title)}" loading="lazy" referrerpolicy="no-referrer">`
      : `<div class="product-placeholder" aria-hidden="true">
           <span class="placeholder-icon">${icon}</span>
           <span class="placeholder-initial">${escapeHtml(initial)}</span>
         </div>`;

    return `
      <article class="product-card ${isOutOfStock ? "out-of-stock" : ""}" data-product-detail="${escapeHtml(product.id)}" role="button" tabindex="0" aria-label="${escapeHtml(product.title)}">
        <div class="product-visual">
          ${visual}
          ${badge ? `<span class="product-badge">${escapeHtml(badge)}</span>` : ""}
          ${isOutOfStock ? `<span class="product-soldout-badge">${t("Sold out")}</span>` : ""}
          ${deliveryEta && !isOutOfStock ? `<span class="product-eta-badge">⚡ ${escapeHtml(deliveryEta)}</span>` : ""}
        </div>
        <div class="product-body">
          <h3 class="product-title">${escapeHtml(product.title)}</h3>
          <div class="product-card-footer">
            <div class="product-price-box">
              ${hasMultiple ? `<span class="product-price-from">${t("From")}</span>` : ""}
              <strong class="product-price">${escapeHtml(priceText)}</strong>
            </div>
            <div class="product-open-cue" aria-hidden="true">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14M12 5l7 7-7 7"/></svg>
            </div>
          </div>
        </div>
      </article>
    `;
  }).join("");

  const empty = !state.catalogBusy && products.length === 0;
  el("emptyCatalog").classList.toggle("hidden", !empty);
  if (empty) {
    el("emptyCatalogTitle").textContent = state.catalogQuery ? t("No matching products") : t("No products available");
    el("emptyCatalogMessage").textContent = state.catalogQuery
      ? t("Try a different search phrase, category, or stock filter.")
      : t("This store has no active products for the selected filters.");
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
    const label = Number.isNaN(date.getTime()) ? "" : new Intl.DateTimeFormat(document.documentElement.lang, { dateStyle: "medium", timeStyle: "short" }).format(date);
    const units = order.items.reduce((sum, item) => sum + item.quantity, 0);
    const fulfillment = order.fulfillment;
    let deliveryHtml = "";
    if (fulfillment?.delivery?.length) {
      deliveryHtml = `
        <div class="delivery-box">
          <div class="delivery-header">
            <span class="delivery-title">${t("📦 Delivered Items")}</span>
          </div>
          <div class="delivery-items">
            ${fulfillment.delivery.map(art => {
              const kind = escapeHtml(art.kind || "CODE");
              const val = escapeHtml(art.value || "");
              const fields = art.fields || {};
              const fieldEntries = Object.entries(fields)
                .map(([k, v]) => `<span class="delivery-subfield"><strong>${escapeHtml(k)}:</strong> ${escapeHtml(v)}</span>`)
                .join(" ");
              return `
                <div class="delivery-item">
                  <div class="delivery-item-top">
                    <span class="delivery-kind-badge kind-${kind.toLowerCase()}">${kind}</span>
                    <button class="copy-artifact-btn" data-copy-val="${val}" type="button">${t("Copy")}</button>
                  </div>
                  <div class="delivery-val-row">
                    <code class="delivery-code">${val}</code>
                  </div>
                  ${fieldEntries ? `<div class="delivery-fields">${fieldEntries}</div>` : ""}
                </div>
              `;
            }).join("")}
          </div>
        </div>
      `;
    } else if (order.status === "PAID" && (!fulfillment || fulfillment.status === "PROCESSING" || fulfillment.status === "PENDING")) {
      deliveryHtml = `
        <div class="delivery-status-box processing">
          <span class="delivery-badge-chip">${t("⏳ FULFILLMENT IN PROGRESS")}</span>
          <p class="muted">${t("Automated fulfillment is in progress. Check back shortly.")}</p>
        </div>
      `;
    }
    return `
      <article class="order-card">
        <div class="order-top">
          <div><span class="order-number">#${escapeHtml(order.order_number)}</span><span class="order-date">${escapeHtml(label)}</span></div>
          <span class="status-chip ${orderStatusClass(order.status)}">${escapeHtml(t(order.status))}</span>
        </div>
        ${deliveryHtml}
        ${order.items.filter(item=>item.warranty_days>0).map(item=>`<details class="warranty-terms"><summary>${t("Warranty")} · ${item.warranty_days} ${t("days")}</summary><p>${escapeHtml(item.warranty_terms)}</p>${order.status==="FULFILLED"?`<button class="text-button" data-order-support="${escapeHtml(order.id)}" data-warranty-item="${escapeHtml(item.id)}">${t("Request warranty review")}</button>`:""}</details>`).join("")}
        <div class="order-divider"></div>
        <div class="order-bottom">
          <div class="order-bottom-meta">
            <span class="order-items-count">${format("items_count",{count:units})}</span>
            <button class="text-button order-support-btn" data-order-support="${escapeHtml(order.id)}" type="button">${t("Get help with this order")}</button>
          </div>
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

function addToCart(variantId, addQuantity = 1) {
  if(state.checkoutBusy)return;
  const variant = findVariant(variantId);
  if (!variant) return;
  if (Number(variant.stock_quantity) <= 0) {
    showToast(t("This option is currently sold out."), "error");
    return;
  }
  const existing = state.cart.get(variantId);
  const maxQuantity = Math.min(100, Number(variant.stock_quantity));
  const newQty = Math.min((existing?.quantity ?? 0) + addQuantity, maxQuantity);
  state.cart.set(variantId, { variant, quantity: newQty });
  state.checkoutKey = null;
  renderCart();
  haptic("light");
  showToast(format("added_to_cart",{name:variant.productTitle}));
}

function changeQuantity(variantId, delta) {
  if(state.checkoutBusy)return;
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
        <button class="quantity-button" data-quantity="-1" data-variant-id="${variantId}" type="button" aria-label=t("Decrease quantity")>−</button>
        <span class="quantity-value">${item.quantity}</span>
        <button class="quantity-button" data-quantity="1" data-variant-id="${variantId}" type="button" aria-label=t("Increase quantity")>+</button>
      </div>
    </div>
  `).join("");

  const totals = cartTotals();
  el("cartTotal").textContent = totals.mixedCurrency ? t("Multiple currencies") : money(totals.amount, totals.currency || "USD");
  el("checkoutButton").disabled = entries.length === 0 || state.checkoutBusy || totals.mixedCurrency;
  el("couponInput").disabled=state.checkoutBusy;recipientInput.disabled=state.checkoutBusy;
  document.querySelectorAll("[data-quantity]").forEach(button=>button.disabled=state.checkoutBusy);
  el("openCartButton").classList.toggle("hidden",totals.units===0);
  el("openCartButton").textContent=`${t("Your cart")} · ${totals.units} · ${money(totals.amount,totals.currency||"USD")}`;

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
  el("productDetailDialog").close();
  closeTopup();
  cartBackdrop.classList.remove("hidden");
  cartSheet.classList.add("open");
  cartSheet.setAttribute("aria-hidden", "false");
  if (tg?.BackButton) tg.BackButton.show();
}

function closeCart() {
  cartSheet.classList.remove("open");
  cartSheet.setAttribute("aria-hidden", "true");
  window.setTimeout(() => {if(!cartSheet.classList.contains("open"))cartBackdrop.classList.add("hidden");}, 260);
  if (tg?.BackButton) tg.BackButton.hide();
}

async function checkout() {
  if (state.checkoutBusy || !state.cart.size) return;
  const recipient = recipientInput.value.trim();
  if (!recipient) {
    showToast(t("Enter the delivery recipient before checkout."), "error");
    recipientInput.focus();
    notifyHaptic("error");
    return;
  }
  const totals = cartTotals();
  if (totals.mixedCurrency) {
    showToast(t("Checkout supports one currency at a time."), "error");
    return;
  }

  state.checkoutKey = state.checkoutKey || (crypto.randomUUID?.() ?? `webapp-${Date.now()}-${Math.random().toString(16).slice(2)}`);
  const payload = {
    items: [...state.cart.values()].map(({ variant, quantity }) => ({ variant_id: variant.id, quantity })),
    recipient,
    coupon_code: el("couponInput").value.trim() || null,
    idempotency_key: state.checkoutKey,
  };

  state.checkoutBusy = true;
  renderCart();
  el("checkoutButton").textContent = t("Processing…");
  try {
    tg?.MainButton?.showProgress?.();
    const order = await pending.run("/api/v1/storefront/checkout", payload);
    state.cart.clear();
    state.checkoutKey = null;
    recipientInput.value = "";
    el("couponInput").value = "";
    closeCart();
    notifyHaptic("success");
    showToast(format("order_confirmed",{number:order.order_number}));
    switchView("orders");
    try{await Promise.all([loadBootstrap(), loadOrders(), loadCatalog(state.selectedCategory)]);}
    catch(_){showToast(t("Order received. Refresh Orders to load its latest status."), "error");}
  } catch (error) {
    notifyHaptic("error");
    showToast(error.message || t("Checkout failed."), "error");
  } finally {
    state.checkoutBusy = false;
    tg?.MainButton?.hideProgress?.();
    el("checkoutButton").textContent = t("Pay from wallet");
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
  if (state.activeTopup?.id && !["SUCCEEDED", "CREDITED", "REVERSED", "SETTLED_REVIEW", "FAILED", "EXPIRED", "CANCELLED"].includes(state.activeTopup.status)) {
    localStorage.setItem(key, `${state.activeTopup._flexible ? "flex:" : ""}${state.activeTopup.id}`);
  } else {
    localStorage.removeItem(key);
  }
}

function selectedTopupProvider() {
  return state.topupOptions.find((provider) => provider.provider_name === topupProviderSelect.value) ?? null;
}

function renderTopupForm(preferredCurrency = null, preferredProvider = null) {
  const providers = state.topupOptions ?? [];
  if (!providers.length) {
    topupProviderSelect.innerHTML = "";
    topupCurrencySelect.innerHTML = "";
    el("topupPolicyHelp").textContent = t("No wallet funding provider is currently available.");
    el("topupSubmitButton").disabled = true;
    return;
  }

  let currentProvider = preferredProvider ? providers.find((p) => p.provider_name === preferredProvider) : null;
  if (!currentProvider) {
    currentProvider = providers.find((p) => p.provider_name === topupProviderSelect.value) ?? providers[0];
  }
  
  topupProviderSelect.innerHTML = providers.map((provider) => `
    <option value="${escapeHtml(provider.provider_name)}" ${provider.provider_name === currentProvider.provider_name ? "selected" : ""}>${escapeHtml(provider.name || provider.display_name || provider.provider_name)}</option>
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
  const flexible = Boolean(currentProvider.flexible_deposits_enabled);
  el("topupFixedFields").classList.toggle("hidden", flexible);
  el("topupPolicyHelp").textContent = flexible
    ? `${t("Choose the asset and amount on the payment page.")} ${currentProvider.auto_credit_enabled ? t("Confirmed funds are credited according to the store's asset/conversion policy.") : t("Confirmed funds require staff review before credit.")}`
    : `${t("Allowed range")}: ${money(currentProvider.min_amount, selectedCurrency)} – ${money(currentProvider.max_amount, selectedCurrency)}.`;
  const termsRow = el("topupTermsRow");
  const termsLink = el("topupTermsLink");
  const termsCheckbox = el("topupTermsCheckbox");
  termsRow.classList.toggle("hidden", !currentProvider.terms_required);
  termsCheckbox.required = Boolean(currentProvider.terms_required);
  if (currentProvider.terms_url) termsLink.href = currentProvider.terms_url;
  else termsLink.removeAttribute("href");

  const quickAmountsContainer = document.querySelector(".quick-amounts");
  if (quickAmountsContainer) {
    const isSYP = selectedCurrency === "SYP";
    const amounts = isSYP ? [10000, 25000, 50000, 100000] : [10, 25, 50, 100];
    const labels = isSYP ? ["10K", "25K", "50K", "100K"] : ["10", "25", "50", "100"];
    quickAmountsContainer.innerHTML = amounts.map((val, idx) => `
      <button type="button" data-topup-amount="${val}">${labels[idx]}</button>
    `).join("");
  }

  el("topupSubmitButton").disabled = state.topupBusy;
}

function topupStatusClass(status) {
  if (["SUCCEEDED", "CREDITED"].includes(status)) return "success";
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
  label.textContent = t(topup.status);
  label.className = `status-chip ${topupStatusClass(topup.status)}`;
  el("topupStatusAmount").textContent = topup._flexible
    ? (topup.credited_amount != null ? `${topup.credited_amount} ${topup.credited_currency || topup.credited_asset || ""}` : `${topup.amount_received ?? "0"} ${topup.asset || "awaiting asset"}`) + (topup.network ? ` · ${topup.network}` : "")
    : money(topup.amount, topup.currency);
  const messages = {
    CREDITED: t("Confirmed deposit credited. Asset and spending-wallet balances are shown separately in your account."),
    SETTLED_REVIEW: t("Deposit confirmed. Staff review is required before wallet credit."),
    REVERSED: t("The provider reversed this deposit. Contact the store for financial review."),
    SUCCEEDED: `Funds confirmed. Wallet balance: ${money(topup.wallet_balance, topup.currency)}.`,
    FAILED: t("The payment provider reported that this payment failed."),
    EXPIRED: t("This payment session expired. Start a new top-up to continue."),
    CANCELLED: t("This payment was cancelled."),
    UNKNOWN: t("Provider status is temporarily uncertain. We will keep checking safely."),
    PROCESSING: t("The provider is processing your payment."),
    PENDING: t("Waiting for the payment provider to confirm settlement."),
    CREATED: t("Payment session created. Continue to the provider to complete it."),
  };
  el("topupStatusMessage").textContent = messages[topup.status] || t("Waiting for payment confirmation.");

  const instructions = topup.payment_instructions;
  el("topupInstructions").textContent = instructions ? [
    instructions.instructions, instructions.asset, instructions.network,
    instructions.destination_address || instructions.pay_address,
    instructions.destination_memo || instructions.payin_extra_id,
    instructions.pay_amount ? `Send exactly ${instructions.pay_amount} ${instructions.pay_currency || ""}` : null,
  ].filter(Boolean).join(" · ") : "";
  el("topupProofForm").classList.toggle("hidden", !instructions || instructions.mode !== "local" || ["SUCCEEDED", "CREDITED", "REVERSED", "SETTLED_REVIEW", "FAILED", "EXPIRED", "CANCELLED"].includes(topup.status));
  const checkoutUrl = safeHttpsUrl(topup.checkout_url);
  el("topupOpenCheckoutButton").classList.toggle("hidden", !checkoutUrl || ["SUCCEEDED", "CREDITED", "REVERSED", "SETTLED_REVIEW"].includes(topup.status));
  el("topupCheckButton").classList.toggle("hidden", ["SUCCEEDED", "CREDITED", "REVERSED", "SETTLED_REVIEW", "FAILED", "EXPIRED", "CANCELLED"].includes(topup.status));
}

function openPaymentCheckout() {
  const checkoutUrl = safeHttpsUrl(state.activeTopup?.checkout_url);
  if (!checkoutUrl) {
    showToast(t("Payment provider did not supply a valid checkout URL."), "error");
    return;
  }
  const isStars = state.activeTopup?.provider === "telegram_stars";
  if (isStars && tg?.openInvoice) {
    try {
      tg.openInvoice(checkoutUrl, (invoiceStatus) => {
        if (invoiceStatus === "paid") {
          window.setTimeout(() => reconcileActiveTopup({ quiet: true }), 350);
        } else if (invoiceStatus === "failed") {
          showToast(t("Telegram could not complete the Stars payment."), "error");
        }
      });
      return;
    } catch (_) {
      showToast(t("Could not open the Telegram Stars invoice."), "error");
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


function openTopup(preferredCurrency = null, preserveActive=false, preferredProvider=null) {
  el("productDetailDialog").close();
  if (!preserveActive && state.activeTopup && ["SUCCEEDED", "CREDITED", "REVERSED", "SETTLED_REVIEW", "FAILED", "EXPIRED", "CANCELLED"].includes(state.activeTopup.status) && !topupSheet.classList.contains("open")) {
    resetTopup();
  }
  if (!state.topupOptions.length && !state.activeTopup) {
    showToast(t("Wallet funding is not configured for this store."), "error");
    return;
  }
  closeCart();
  topupBackdrop.classList.remove("hidden");
  topupSheet.classList.add("open");
  topupSheet.setAttribute("aria-hidden", "false");
  if (!state.activeTopup) {
    renderTopupForm(preferredCurrency, preferredProvider);
    renderTopupStatus();
  } else {
    renderTopupStatus();
  }
  tg?.BackButton?.show?.();
}

function closeTopup() {
  topupSheet.classList.remove("open");
  topupSheet.setAttribute("aria-hidden", "true");
  window.setTimeout(() => {if(!topupSheet.classList.contains("open"))topupBackdrop.classList.add("hidden");}, 260);
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
    const flexible = Boolean(state.activeTopup._flexible);
    state.activeTopup = { ...await api(`/api/v1/storefront/wallet/${flexible ? "flexible-deposits" : "topups"}/${state.activeTopup.id}/reconcile`, { method: "POST" }), _flexible: flexible };
    renderTopupStatus();
    persistActiveTopup();
    if (["SUCCEEDED", "CREDITED"].includes(state.activeTopup.status)) {
      stopTopupPolling();
      await loadBootstrap();
      notifyHaptic("success");
      showToast(t("Payment confirmed. Your account balances have been refreshed."));
    } else if (["FAILED", "EXPIRED", "CANCELLED", "REVERSED", "SETTLED_REVIEW"].includes(state.activeTopup.status)) {
      stopTopupPolling();
      if (!quiet) showToast(state.activeTopup.status === "SETTLED_REVIEW" ? t("Deposit confirmed. Staff review is required before wallet credit.") : t("Payment did not complete."), "error");
    }
  } catch (error) {
    if (!quiet) showToast(error.message || t("Could not check payment status."), "error");
  } finally {
    state.topupBusy = false;
    el("topupCheckButton").disabled = false;
  }
}

function startTopupPolling() {
  stopTopupPolling();
  if (!state.activeTopup?.id || ["SUCCEEDED", "CREDITED", "REVERSED", "SETTLED_REVIEW", "FAILED", "EXPIRED", "CANCELLED"].includes(state.activeTopup.status)) return;
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
  const flexible = Boolean(provider?.flexible_deposits_enabled);
  if (!provider || (!flexible && (!currency || !/^\d+(?:\.\d{1,2})?$/.test(rawAmount) || !Number.isFinite(amount)))) {
    showToast(t("Choose a provider, currency, and valid amount."), "error");
    return;
  }
  const min = Number(provider.min_amount);
  const max = Number(provider.max_amount);
  if (provider.whole_units_only && !Number.isInteger(amount)) {
    showToast(t("This payment method requires a whole-number amount."), "error");
    return;
  }
  if (provider.terms_required && !el("topupTermsCheckbox").checked) {
    showToast(t("Accept the payment terms before continuing."), "error");
    return;
  }
  if (!flexible && (amount < min || amount > max)) {
    showToast(format("amount_range",{min:money(min,currency),max:money(max,currency)}), "error");
    return;
  }

  state.topupKey = state.topupKey || (crypto.randomUUID?.() ?? `topup-${Date.now()}-${Math.random().toString(16).slice(2)}`);
  state.topupBusy = true;
  el("topupSubmitButton").disabled = true;
  el("topupSubmitButton").textContent = t("Creating payment…");
  try {
    const path = flexible ? "flexible-deposits" : (provider.id ? "topups/method" : "topups");
    const body = { idempotency_key: state.topupKey };
    if (provider.id) body.payment_method_id = provider.id;
    else { body.provider_name = provider.provider_name; body.terms_accepted = Boolean(el("topupTermsCheckbox").checked); }
    if (!flexible) { body.amount = amount.toFixed(2); body.currency = currency; }
    state.activeTopup = { ...await pending.run(`/api/v1/storefront/wallet/${path}`, body), _flexible: flexible };
    persistActiveTopup();
    renderTopupStatus();
    haptic("medium");
    if (safeHttpsUrl(state.activeTopup.checkout_url)) openPaymentCheckout();
    startTopupPolling();
  } catch (error) {
    notifyHaptic("error");
    showToast(error.message || t("Could not create wallet top-up."), "error");
  } finally {
    state.topupBusy = false;
    el("topupSubmitButton").disabled = false;
    el("topupSubmitButton").textContent = t("Continue to payment");
  }
}

async function restorePendingTopup() {
  const key = topupStorageKey();
  const intentId = key ? localStorage.getItem(key) : null;
  if (!intentId) return;
  try {
    const flexible = intentId.startsWith("flex:");
    const id = flexible ? intentId.slice(5) : intentId;
    state.activeTopup = { ...await api(`/api/v1/storefront/wallet/${flexible ? "flexible-deposits" : "topups"}/${encodeURIComponent(id)}`), _flexible: flexible };
    persistActiveTopup();
    if (!["SUCCEEDED", "CREDITED", "REVERSED", "SETTLED_REVIEW", "FAILED", "EXPIRED", "CANCELLED"].includes(state.activeTopup.status)) {
      startTopupPolling();
    }
  } catch (_) {
    localStorage.removeItem(key);
    state.activeTopup = null;
  }
}

async function submitPaymentProof(event) {
  event.preventDefault();
  if (!state.activeTopup?.id || state.topupBusy) return;
  const button = el("topupProofSubmit");
  button.disabled = true;
  try {
    const mode = state.activeTopup.payment_instructions?.verification_mode;
    await api(`/api/v1/storefront/wallet/topups/${state.activeTopup.id}/observations`, {
      method: "POST", body: JSON.stringify({
        source: ["ONCHAIN", "HYBRID"].includes(mode) ? "ONCHAIN" : "MANUAL",
        external_reference: el("topupProofReference").value.trim() || null,
        note: el("topupProofNote").value.trim() || null,
      }),
    });
    showToast(t("Reference submitted for verification. Credit follows confirmation."));
  } catch (error) { showToast(error.message || t("Could not submit reference."), "error"); }
  finally { button.disabled = false; }
}

async function openHistoricalPayment(id,kind){
  if(state.topupBusy)return;
  const flexible=kind==="flexible";
  const payment=await api(`/api/v1/storefront/wallet/${flexible?"flexible-deposits":"topups"}/${encodeURIComponent(id)}`);
  stopTopupPolling();state.activeTopup={...payment,_flexible:flexible};
  openTopup(null,true);startTopupPolling();
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
  if(el("productDetailDialog").open){el("productDetailDialog").close();return;}
  if (topupSheet.classList.contains("open")) closeTopup();
  else if (cartSheet.classList.contains("open")) closeCart();
}

function switchView(target) {
  if (target === "account" || target === "help") target = "settings";
  document.querySelectorAll(".view").forEach((view) => view.classList.toggle("active", view.dataset.view === target || view.dataset.aliasView === target));
  document.querySelectorAll(".nav-button").forEach((button) => button.classList.toggle("active", button.dataset.target === target || (target === "settings" && button.dataset.target === "account")));
  if (target === "wallet") {
    experience?.history();
    renderTopupForm();
  }
  if (target === "settings") {
    experience?.renderHelp();
    customerCare?.list().catch(error=>showToast(error.message,"error"));
  }
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

    const copyButton = event.target.closest(".copy-artifact-btn");
    if (copyButton) {
      const text = copyButton.dataset.copyVal || "";
      if (text) {
        try {
          await navigator.clipboard.writeText(text);
          const orig = copyButton.textContent;
          copyButton.textContent = t("Copied!");
          copyButton.classList.add("copied");
          setTimeout(() => {
            copyButton.textContent = orig;
            copyButton.classList.remove("copied");
          }, 2000);
          showToast(t("Copied to clipboard!"));
          haptic("medium");
        } catch (_) {
          showToast(text);
        }
      }
      return;
    }

    const navButton = event.target.closest(".nav-button[data-target]");
    if (navButton) switchView(navButton.dataset.target);
  });

  experience=bindExperience({state,api,esc:escapeHtml,money,t,format,toast:showToast,openPayment:openHistoricalPayment,addToCart,openCart});
  customerCare=bindCustomerCare({api,escapeHtml,toast:showToast,orders:()=>state.orders||[],t});
  bindSheetAccess(cartSheet,closeCart);bindSheetAccess(topupSheet,closeTopup);
  el("shopRecharge").onclick=()=>switchView("wallet");
  const walletRecharge = el("walletRechargeButton");
  if (walletRecharge) walletRecharge.onclick=()=>openTopup();
  if (el("shopSupport")) {
    el("shopSupport").onclick=()=>{
      switchView("settings");
      el("supportSection")?.scrollIntoView({behavior:"smooth"});
    };
  }
  const helpBtn = el("accountHelpButton");
  if (helpBtn) {
    helpBtn.onclick=()=>{
      el("supportSection")?.scrollIntoView({behavior:"smooth"});
    };
  }
  el("storeLanguage").addEventListener("change",()=>{setLanguage(el("storeLanguage").value,state.bootstrap?.store?.id,true);renderBootstrap();renderCategories();renderProducts();renderOrders();renderCart();});
  el("profileButton").addEventListener("click", () => switchView("settings"));
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
  el("ordersMore").addEventListener("click",()=>loadOrders(true).catch(error=>showToast(error.message,"error")));
  el("refreshOrdersButton").addEventListener("click", () => loadOrders().catch((error) => showToast(error.message, "error")));
  el("openCartButton").addEventListener("click",openCart);
  el("closeCartButton").addEventListener("click", closeCart);
  cartBackdrop.addEventListener("click", closeCart);
  el("checkoutButton").addEventListener("click", checkout);
  el("couponInput").addEventListener("input", () => { state.checkoutKey = null; });
  recipientInput.addEventListener("input", () => { state.checkoutKey = null; });
  el("retryButton").addEventListener("click", () => window.location.reload());

  el("fundWalletButton")?.addEventListener("click", () => openTopup());
  el("closeTopupButton").addEventListener("click", closeTopup);
  topupBackdrop.addEventListener("click", closeTopup);
  el("topupSubmitButton").addEventListener("click", createTopup);
  el("topupCheckButton").addEventListener("click", () => reconcileActiveTopup());
  el("topupOpenCheckoutButton").addEventListener("click", openPaymentCheckout);
  el("topupProofForm").addEventListener("submit", submitPaymentProof);
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
    const initialThemes = await StoreThemes.ready.catch(()=>null);
    if(initialThemes)StoreThemes.apply(document.documentElement,initialThemes,
      previewMode?new URLSearchParams(location.search).get("theme")||"emerald":"emerald");
    await authenticate();
    await loadBootstrap();
    pending=previewMode?{run:(path,payload)=>api(path,{method:"POST",body:JSON.stringify(payload)}),render:()=>{}}:pendingOperations({state,api,t,onRecovered:async(path,result)=>{
      if(path.endsWith('/checkout')){state.cart.clear();state.checkoutKey=null;renderCart();closeCart();await Promise.all([loadBootstrap(),loadOrders()]);switchView('orders');}
      else{state.activeTopup={...result,_flexible:path.endsWith('/flexible-deposits')};persistActiveTopup();openTopup(undefined,true);renderTopupStatus();startTopupPolling();}
    }});
    pending.render();
    await Promise.all([loadCatalog(), loadOrders(), loadTopupOptions()]);
    renderBootstrap();
    if(!previewMode)await restorePendingTopup();
    document.documentElement.classList.remove("booting");
    loadingView.classList.add("hidden");
    errorView.classList.add("hidden");
    authenticatedApp.classList.remove("hidden");
    bottomNav.classList.remove("hidden");
    document.getElementById("app").setAttribute("aria-busy", "false");
    renderCart();
    const entry = new URLSearchParams(location.search).get("view");
    if (["account","orders","settings","wallet"].includes(entry)) switchView(entry);
    if (entry === "support" || entry === "help") {
      switchView("settings");
      el("supportSection")?.scrollIntoView({behavior:"smooth"});
    }
    if (entry === "recharge") switchView("wallet");
  } catch (error) {
    console.error("Mini App bootstrap failed", error);
    setFatalError(t("Unable to open the store"), error.message || "The storefront could not be initialized.");
  }
}

localizeStatic();
boot();
