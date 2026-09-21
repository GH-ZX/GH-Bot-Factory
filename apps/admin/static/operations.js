/* Tenant operations use the same authenticated API boundary as the rest of Admin. */
const Operations = (() => {
  let tab = "support",
    entries = [],
    submitAction = null,
    offset = 0,
    loadVersion = 0;
  const esc = escapeHtml;
  const root = "/api/v1/admin/operations";
  const manage = () =>
    ["OWNER", "ADMIN"].includes(state.bootstrap?.actor?.role);
  const request = (path, body, method = "POST") =>
    api(path, { method, body: JSON.stringify(body) });
  const button = (action, text, id = "") =>
    `<button type="button" class="ghost" data-op="${action}" data-id="${esc(id)}">${text}</button>`;
  const input = (name, label, value = "", type = "text", extra = "") =>
    `<label>${label}<input name="${name}" type="${type}" value="${esc(value)}" ${extra}></label>`;
  const area = (name, label, value = "", extra = "") =>
    `<label>${label}<textarea name="${name}" rows="4" ${extra}>${esc(value)}</textarea></label>`;
  const select = (name, label, options) =>
    `<label>${label}<select name="${name}">${options.map(([v, t]) => `<option value="${esc(v)}">${esc(t)}</option>`).join("")}</select></label>`;
  const note = (text) => `<p class="scope-note">${text}</p>`;
  const memberOptions = (members) =>
    members.map((m) => [
      m.user_id,
      [m.first_name, m.last_name].filter(Boolean).join(" ") ||
        m.username ||
        m.email ||
        `Customer ${m.user_id.slice(0, 8)}`,
    ]);
  const card = (title, detail, actions = "") =>
    `<article class="brief-card"><div class="item-row"><div><h3>${esc(title)}</h3><p class="muted">${detail}</p></div><div class="ops-actions">${actions}</div></div></article>`;
  function modal(title, html, action, label = "Save") {
    el("operationsTitle").textContent = title;
    el("operationsFields").innerHTML = html;
    el("operationsError").textContent = "";
    el("saveOperations").textContent = label;
    el("saveOperations").hidden = !action;
    el("saveOperations").disabled = false;
    submitAction = action;
    if (!el("operationsDialog").open) el("operationsDialog").showModal();
  }
  async function load() {
    const version = ++loadVersion;
    let rows;
    el("opsError").textContent = "";
    el("opsContent").innerHTML =
      '<p class="muted" role="status">Loading store operations…</p>';
    try {
      const currentTab = tab;
      let html = "";
      if (tab === "support") {
        rows = await api(`${root}/cases?offset=${offset}`);
        html =
          note(
            "Warranty approvals record a decision. Any refund or replacement must be completed through the existing order workflow and documented here.",
          ) +
          (manage() ? button("new-case", "Open a case") : "") +
          rows
            .map((r) =>
              card(
                r.subject,
                `${esc(r.kind)} · ${esc(r.status)} · ${new Date(r.created_at).toLocaleDateString()}`,
                button("case", "View conversation", r.id),
              ),
            )
            .join("") +
          `<div class="ops-actions">${offset ? button("previous", "Previous") : ""}${rows.length === 100 ? button("next", "Next") : ""}</div>`;
      } else if (tab === "coupons") {
        rows = await api(`${root}/coupons`);
        html =
          note(
            "Percentage discounts are checked at checkout. Currency, minimum spend, expiry and usage limits are enforced by the server. Latest 200 coupons.",
          ) +
          (manage() ? button("new-coupon", "Create coupon") : "") +
          rows
            .map((r) =>
              card(
                r.code,
                `${esc(r.percent)}% off · ${esc(r.currency)} · ${r.used_count}/${r.max_uses} used · expires ${new Date(r.expires_at).toLocaleDateString()} · ${r.is_active ? "Active" : "Paused"}`,
                manage()
                  ? button(
                      "coupon-toggle",
                      r.is_active ? "Pause" : "Activate",
                      r.id,
                    )
                  : "",
              ),
            )
            .join("");
      } else if (tab === "announcements") {
        rows = await api(`${root}/announcements`);
        html =
          note(
            "Draft first, review, then queue delivery to people who have used your bot. Unknown sends need review and are never automatically repeated. Latest 100 announcements.",
          ) +
          (manage() ? button("new-announcement", "Write announcement") : "") +
          rows
            .map((r) =>
              card(
                r.title,
                `${esc(r.status)} · ${esc(r.audience)}<br>${Object.entries(
                  r.deliveries,
                )
                  .map(([k, v]) => `${esc(k)}: ${v}`)
                  .join(" · ")}<br>${esc(r.body)}`,
                button("announcement-review", "Review", r.id),
              ),
            )
            .join("");
      } else if (tab === "catalog") {
        rows = await api("/api/v1/admin/catalog/categories");
        html =
          note(
            "Lower display positions appear first. Rename and reorder products from Catalog → Edit product.",
          ) +
          (manage() ? button("new-category", "Add category") : "") +
          rows
            .map((r) =>
              card(
                r.name,
                `Position ${r.sort_order} · ${r.is_active ? "Visible" : "Hidden"}`,
                manage() ? button("category", "Edit", r.id) : "",
              ),
            )
            .join("");
      } else {
        const [tiers, rules] = await Promise.all([
          api("/api/v1/admin/economics/pricing-tiers"),
          api("/api/v1/admin/economics/pricing-rules"),
        ]);
        rows = { tiers, rules };
        html =
          note(
            "Resellers are customers assigned to a pricing tier. Rules add a markup to supplier cost; without a usable supplier cost, the catalog price applies. Set base selling prices in Catalog → Edit product → Variants.",
          ) +
          (manage()
            ? `<div class="ops-actions">${button("new-tier", "Create reseller tier")}${button("assign-tier", "Assign customer")}${button("new-rule", "Create margin rule")}</div>`
            : "") +
          "<h3>Customer tiers</h3>" +
          tiers
            .map((r) =>
              card(
                r.display_name,
                `${esc(r.code)} · ${r.is_active ? "Active" : "Paused"}`,
                manage()
                  ? button(
                      "tier-toggle",
                      r.is_active ? "Pause" : "Activate",
                      r.id,
                    )
                  : "",
              ),
            )
            .join("") +
          "<h3>Margin rules</h3>" +
          rules
            .map((r) =>
              card(
                r.name,
                `${esc(r.scope)} · ${esc(r.markup_mode)} · ${esc(r.markup_percent)}% + ${esc(r.markup_fixed)} · ${r.is_active ? "Active" : "Paused"}`,
                manage()
                  ? button(
                      "rule-toggle",
                      r.is_active ? "Pause" : "Activate",
                      r.id,
                    )
                  : "",
              ),
            )
            .join("");
      }
      if (currentTab !== tab || version !== loadVersion) return;
      entries = rows;
      el("opsContent").innerHTML =
        `<div class="ops-grid">${html}${Array.isArray(rows) && !rows.length ? '<div class="empty">Nothing here yet. Create your first record to get started.</div>' : ""}</div>`;
    } catch (err) {
      el("opsContent").innerHTML = "";
      el("opsError").textContent = err.message;
    }
  }
  async function showCase(id) {
    const c = await api(`${root}/cases/${id}`);
    const closed = ["RESOLVED", "DECLINED"].includes(c.status);
    const transitions =
      c.status === "APPROVED"
        ? [["RESOLVED", "Resolved"]]
        : [
            ["IN_PROGRESS", "In progress"],
            ...(c.kind === "WARRANTY" && manage()
              ? [
                  ["APPROVED", "Approve warranty"],
                  ["DECLINED", "Decline warranty"],
                ]
              : []),
            ["RESOLVED", "Resolved"],
          ];
    modal(
      c.subject,
      note(
        `${esc(c.kind)} · ${esc(c.status)}${c.warranty_terms ? ` · Purchase terms: ${esc(c.warranty_terms)}` : ""}`,
      ) +
        c.messages
          .map(
            (m) =>
              `<article class="ops-message ${m.is_staff ? "staff" : ""}"><strong>${m.is_staff ? "Store team" : "Customer"}</strong><small> · ${new Date(m.created_at).toLocaleString()}</small><p>${esc(m.body)}</p></article>`,
          )
          .join("") +
        (closed
          ? ""
          : area(
              "body",
              "Reply / resolution",
              "",
              'required maxlength="3000"',
            ) +
            select("status", "Update status", [
              ["", "Keep current status"],
              ...transitions.filter(([v]) => v !== c.status),
            ])),
      closed
        ? null
        : async (data) =>
            request(`${root}/cases/${id}/reply`, {
              body: data.body,
              expected_version: c.version,
              status: data.status || null,
            }),
      "Save reply",
    );
  }
  async function newCase() {
    const [members, orders] = await Promise.all([
      api("/api/v1/admin/members?limit=100"),
      api("/api/v1/admin/orders?limit=100"),
    ]);
    modal(
      "Open a support or warranty case",
      select("user_id", "Customer", memberOptions(members.members)) +
        input(
          "subject",
          "Subject",
          "",
          "text",
          'required minlength="3" maxlength="160"',
        ) +
        select("order_id", "Related order", [
          ["", "General support"],
          ...orders.orders.map((o) => [
            o.id,
            `${o.order_number} · ${o.customer_name}`,
          ]),
        ]) +
        select("warranty_item_id", "Warranty claim", [["", "Support only"]]) +
        area("body", "Message", "", 'required maxlength="3000"') +
        note(
          "Only fulfilled orders with an unexpired purchase-time warranty are eligible. The server checks that the order belongs to the chosen customer. This list shows the latest 100 customers and orders.",
        ),
      (data) =>
        request(`${root}/cases`, {
          ...data,
          order_id: data.order_id || null,
          warranty_item_id: data.warranty_item_id || null,
        }),
    );
    const orderSelect =
      el("operationsFields").querySelector('[name="order_id"]');
    orderSelect.addEventListener("change", () => {
      const o = orders.orders.find((x) => x.id === orderSelect.value);
      const options = [
        ["", "Support only"],
        ...(o?.status === "FULFILLED"
          ? o.items.map((i, n) => [
              i.id,
              `Claim for item ${n + 1} · ${i.quantity} × ${i.unit_price} ${o.currency}`,
            ])
          : []),
      ];
      el("operationsFields").querySelector(
        '[name="warranty_item_id"]',
      ).innerHTML = options
        .map(([v, t]) => `<option value="${esc(v)}">${esc(t)}</option>`)
        .join("");
      if (o)
        el("operationsFields").querySelector('[name="user_id"]').value =
          o.user_id;
    });
  }
  async function action(name, id) {
    const row = Array.isArray(entries)
      ? entries.find((r) => r.id === id)
      : null;
    if (name === "next" || name === "previous") {
      offset = Math.max(0, offset + (name === "next" ? 100 : -100));
      return load();
    }
    if (name === "case") return showCase(id);
    if (!manage())
      throw new Error("An owner or admin can manage this setting.");
    if (name === "new-case") return newCase();
    if (name === "new-coupon")
      return modal(
        "Create a coupon",
        input(
          "code",
          "Code",
          "",
          "text",
          'required pattern="[A-Za-z0-9_-]{2,40}"',
        ) +
          input(
            "percent",
            "Discount (%)",
            "10",
            "number",
            'required min="0.01" max="90" step="0.01"',
          ) +
          input(
            "currency",
            "Wallet currency",
            "USD",
            "text",
            'required pattern="[A-Z]{3}"',
          ) +
          input(
            "minimum_amount",
            "Minimum order amount",
            "0",
            "number",
            'required min="0" step="0.01"',
          ) +
          input(
            "max_uses",
            "Maximum uses",
            "100",
            "number",
            'required min="1" max="1000000"',
          ) +
          input(
            "expires_at",
            "Expiry (your local time)",
            "",
            "datetime-local",
            "required",
          ),
        (data) =>
          request(`${root}/coupons`, {
            ...data,
            max_uses: Number(data.max_uses),
            expires_at: new Date(data.expires_at).toISOString(),
          }),
      );
    if (name === "coupon-toggle") {
      await request(
        `${root}/coupons/${id}`,
        { is_active: !row.is_active },
        "PATCH",
      );
      return load();
    }
    if (name === "new-category" || name === "category")
      return modal(
        row ? "Edit category" : "New category",
        input(
          "name",
          "Name",
          row?.name || "",
          "text",
          'required maxlength="100"',
        ) +
          input(
            "slug",
            "Web label",
            row?.slug || "",
            "text",
            'required pattern="[a-z0-9]+(-[a-z0-9]+)*"',
          ) +
          input(
            "sort_order",
            "Display position",
            row?.sort_order || 0,
            "number",
            'min="0" max="100000" required',
          ) +
          select(
            "is_active",
            "Visibility",
            row?.is_active === false
              ? [
                  ["false", "Hidden"],
                  ["true", "Visible"],
                ]
              : [
                  ["true", "Visible"],
                  ["false", "Hidden"],
                ],
          ),
        (data) =>
          request(
            `/api/v1/admin/catalog/categories${id ? `/${id}` : ""}`,
            {
              ...data,
              sort_order: Number(data.sort_order),
              is_active: data.is_active === "true",
            },
            id ? "PATCH" : "POST",
          ),
      );
    if (name === "new-announcement") {
      const bots = await api("/api/v1/admin/bots/fleet");
      return modal(
        "Write announcement",
        select(
          "bot_id",
          "Bot",
          (bots.bots || bots)
            .filter((b) => b.is_enabled)
            .map((b) => [
              b.id,
              b.display_name || b.username || b.name || b.id.slice(0, 8),
            ]),
        ) +
          input(
            "title",
            "Internal title",
            "",
            "text",
            'required minlength="2" maxlength="120"',
          ) +
          select("audience", "Audience", [
            ["ALL", "All reachable customers"],
            ["BUYERS", "Customers with completed purchases"],
          ]) +
          area("body", "Telegram message", "", 'required maxlength="3500"') +
          note(
            "Saved as a draft. The message is sent as plain text exactly as written; no customer messages are sent until you review and queue it.",
          ),
        (data) => request(`${root}/announcements`, data),
        "Save draft",
      );
    }
    if (name === "announcement-review")
      return modal(
        row.title,
        `<div class="ops-message">${esc(row.body)}</div>` +
          note(
            `${esc(row.audience)} · ${esc(row.status)}. Queueing sends real Telegram messages. Cancelling stops pending deliveries; a message already in flight may still arrive.`,
          ) +
          (row.status === "QUEUED"
            ? button("announcement-cancel", "Cancel pending deliveries", row.id)
            : ""),
        row.status === "DRAFT"
          ? () => request(`${root}/announcements/${id}/queue`, {})
          : null,
        "Queue announcement",
      );
    if (name === "announcement-cancel") {
      await request(`${root}/announcements/${id}/cancel`, {});
      el("operationsDialog").close();
      return load();
    }
    if (name === "new-tier")
      return modal(
        "Create reseller tier",
        input(
          "display_name",
          "Tier name",
          "",
          "text",
          'required maxlength="120"',
        ) +
          input(
            "code",
            "Short label",
            "",
            "text",
            'required pattern="[a-z0-9][a-z0-9_-]{1,63}"',
          ) +
          note(
            "Assign customers to this tier, then create a margin rule for it. A tier alone does not change prices.",
          ),
        (data) => request("/api/v1/admin/economics/pricing-tiers", data),
      );
    if (name === "assign-tier") {
      const members = await api("/api/v1/admin/members?limit=100");
      return modal(
        "Assign a customer to a tier",
        select("user_id", "Customer", memberOptions(members.members)) +
          select(
            "tier_id",
            "Pricing tier",
            entries.tiers
              .filter((t) => t.is_active)
              .map((t) => [t.id, t.display_name]),
          ),
        (data) =>
          request("/api/v1/admin/economics/pricing-tier-assignment", data),
      );
    }
    if (name === "new-rule")
      return modal(
        "Create margin rule",
        input("name", "Rule name", "", "text", 'required maxlength="120"') +
          select("tier_id", "Applies to tier", [
            ["", "All customers"],
            ...entries.tiers
              .filter((t) => t.is_active)
              .map((t) => [t.id, t.display_name]),
          ]) +
          select("markup_mode", "Markup method", [
            ["PERCENT", "Percentage above supplier cost"],
            ["FIXED", "Fixed amount above supplier cost"],
          ]) +
          input(
            "value",
            "Markup value",
            "10",
            "number",
            'required min="0" step="0.01"',
          ) +
          input(
            "minimum_margin",
            "Minimum margin",
            "0",
            "number",
            'required min="0" step="0.01"',
          ) +
          input(
            "priority",
            "Priority (lower takes precedence)",
            "100",
            "number",
            'required min="0" max="100000"',
          ) +
          note(
            "This rule covers the whole catalog. Fixed markup is added to supplier cost; it is not a fixed selling price.",
          ),
        (data) =>
          request("/api/v1/admin/economics/pricing-rules", {
            name: data.name,
            scope: "GLOBAL",
            tier_id: data.tier_id || null,
            markup_mode: data.markup_mode,
            markup_percent: data.markup_mode === "PERCENT" ? data.value : "0",
            markup_fixed: data.markup_mode === "FIXED" ? data.value : "0",
            minimum_margin: data.minimum_margin,
            priority: Number(data.priority),
          }),
      );
    if (name === "tier-toggle" || name === "rule-toggle") {
      const kind = name === "tier-toggle" ? "tiers" : "rules",
        item = entries[kind].find((r) => r.id === id);
      await request(
        `/api/v1/admin/economics/pricing-${kind}/${id}`,
        { is_active: !item.is_active },
        "PATCH",
      );
      return load();
    }
    if (name === "variant") return editVariant(id);
  }
  function productFields(product) {
    let host = el("productExtras");
    if (!host) {
      host = document.createElement("div");
      host.id = "productExtras";
      el("variantFields").before(host);
    }
    const meta = product?.metadata || {};
    host.innerHTML =
      input(
        "image_url",
        "Product image URL",
        meta.image_url || "",
        "url",
        'placeholder="https://…"',
      ) +
      input(
        "sort_order",
        "Display position",
        product?.sort_order || 0,
        "number",
        'min="0" max="100000" required',
      ) +
      input(
        "warranty_days",
        "Warranty duration (days; 0 = none)",
        meta.warranty_days || 0,
        "number",
        'min="0" max="3650" required',
      ) +
      area(
        "warranty_terms",
        "Warranty terms",
        meta.warranty_terms || "",
        'maxlength="2000"',
      ) +
      (product
        ? `<h3>Variants & base prices</h3>${product.variants.map((v) => card(v.title, `${esc(v.price)} ${esc(v.currency)} · Stock ${v.stock_quantity}`, button("variant", "Edit price / stock", v.id))).join("")}`
        : "");
    host
      .querySelectorAll('[data-op="variant"]')
      .forEach((b) =>
        b.addEventListener("click", () =>
          editVariant(b.dataset.id).catch((err) => alert(err.message)),
        ),
      );
  }
  function productPayload(id) {
    const value = (n) =>
      el("productExtras").querySelector(`[name="${n}"]`).value;
    return {
      sort_order: Number(value("sort_order")),
      metadata: {
        ...(state.products.find((p) => p.id === id)?.metadata || {}),
        image_url: value("image_url") || null,
        warranty_days: Number(value("warranty_days")),
        warranty_terms: value("warranty_terms"),
      },
    };
  }
  async function editVariant(id) {
    const product = state.products.find((p) =>
        p.variants.some((v) => v.id === id),
      ),
      v = product.variants.find((v) => v.id === id);
    modal(
      "Edit variant",
      input("title", "Name", v.title, "text", 'required maxlength="100"') +
        input(
          "price",
          "Base selling price",
          v.price,
          "number",
          'min="0.01" step="0.01" required',
        ) +
        input(
          "stock_quantity",
          "Stock quantity",
          v.stock_quantity,
          "number",
          'min="0" required',
        ) +
        select(
          "is_active",
          "Availability",
          v.is_active
            ? [
                ["true", "Active"],
                ["false", "Hidden"],
              ]
            : [
                ["false", "Hidden"],
                ["true", "Active"],
              ],
        ) +
        note(
          `Currency: ${esc(v.currency)}. Supplier margin rules may override this base price.`,
        ),
      async (data) => {
        await request(
          `/api/v1/admin/catalog/variants/${id}`,
          {
            ...data,
            stock_quantity: Number(data.stock_quantity),
            is_active: data.is_active === "true",
          },
          "PATCH",
        );
        await loadProducts();
        productFields(state.products.find((p) => p.id === product.id));
      },
    );
  }
  document.querySelectorAll("[data-ops-tab]").forEach((b) =>
    b.addEventListener("click", () => {
      tab = b.dataset.opsTab;
      offset = 0;
      document
        .querySelectorAll("[data-ops-tab]")
        .forEach((x) => x.setAttribute("aria-selected", String(x === b)));
      load();
    }),
  );
  for (const id of ["closeOperations", "cancelOperations"])
    el(id).addEventListener("click", () => el("operationsDialog").close());
  for (const id of ["opsContent", "operationsFields"])
    el(id).addEventListener("click", (event) => {
      const b = event.target.closest("[data-op]");
      if (!b) return;
      action(b.dataset.op, b.dataset.id).catch((err) => {
        el(
          el("operationsDialog").open ? "operationsError" : "opsError",
        ).textContent = err.message;
      });
    });
  el("operationsForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!submitAction) return;
    const save = el("saveOperations");
    save.disabled = true;
    el("operationsError").textContent = "";
    try {
      await submitAction(Object.fromEntries(new FormData(event.target)));
      el("operationsDialog").close();
      if (document.querySelector(".nav.active")?.dataset.view === "operations")
        await load();
    } catch (err) {
      el("operationsError").textContent = err.message;
    } finally {
      save.disabled = false;
    }
  });
  return { load, productFields, productPayload };
})();
