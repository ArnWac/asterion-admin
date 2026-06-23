// Entrypoint module.
//
// Both app.html and login.html load this file. We dispatch on
// `body.dataset.view`, dynamically import the matching view module, and
// hand it the `#app-root` element plus any URL-derived arguments the
// server already put into window.ASTERION.
//
// We also wire up the shell-wide concerns: the sign-out button, the
// sidebar resource navigation, the bottom user line, and an
// unauthenticated -> /login redirect for app pages.

import { admin, APIError, auth, root, tenantStore, tokenStore } from "./api.js";
import { getFullContract } from "./contract.js";
import { el, mount, showToast } from "./dom.js";
import { renderImpersonationBanner } from "./impersonation.js";

const cfg = window.ASTERION || {};

const viewLoaders = {
  login: () => import("./views/login.js").then((m) => m.mountLogin()),
  "login-complete": () =>
    import("./views/login_complete.js").then((m) => m.mountLoginComplete()),
  dashboard: (root) => import("./views/dashboard.js").then((m) => m.mountDashboard(root)),
  list: (root) => import("./views/list.js").then((m) => m.mountList(root, cfg.resource)),
  detail: (root) =>
    import("./views/detail.js").then((m) => m.mountDetail(root, cfg.resource, cfg.recordId)),
  create: (root) =>
    import("./views/form.js").then((m) => m.mountForm(root, cfg.resource, "create", null)),
  edit: (root) =>
    import("./views/form.js").then((m) => m.mountForm(root, cfg.resource, "edit", cfg.recordId)),
  delete: (root) =>
    import("./views/delete.js").then((m) => m.mountDelete(root, cfg.resource, cfg.recordId)),
  settings: (root) => import("./views/settings.js").then((m) => m.mountSettings(root)),
  permissions: (root) =>
    import("./views/permission_matrix.js").then((m) => m.mountPermissionMatrix(root)),
  page: (root) =>
    import("./views/page.js").then((m) => m.mountPage(root, cfg.pageModule, cfg.pageId)),
};

async function main() {
  const view = document.body.dataset.view || cfg.view;

  if (view === "login") {
    await viewLoaders.login();
    return;
  }

  // login-complete runs BEFORE the not-logged-in redirect: at this
  // point tokenStore is empty by definition (the OAuth flow just
  // landed here to populate it). The view mounts, reads the fragment,
  // and either stores+redirects or bails to /login on its own.
  if (view === "login-complete") {
    await viewLoaders["login-complete"]();
    return;
  }

  if (!tokenStore.isLoggedIn()) {
    window.location.href = `${cfg.uiPath}/login`;
    return;
  }

  wireSignout();
  // Sidebar nav + user line are non-essential; failure shouldn't break the view.
  renderImpersonationBanner().catch(() => {});
  populateTenantSwitcher().catch(() => {});
  populateSidebarNav().catch(() => {});
  populateSidebarExtensions().catch(() => {});
  populateUserLine().catch(() => {});
  highlightSettingsLink();

  const root = document.getElementById("app-root");
  if (!root) return;
  const loader = viewLoaders[view];
  if (!loader) {
    mount(root, el("div", { class: "card" }, el("p", {}, `Unknown view: ${view}`)));
    return;
  }
  try {
    await loader(root);
  } catch (err) {
    const message = err instanceof APIError ? err.message : String(err);
    mount(
      root,
      el(
        "div",
        { class: "card" },
        el("p", { class: "form-error" }, `Failed to load view: ${message}`)
      )
    );
  }
}

function wireSignout() {
  const button = document.getElementById("signout");
  if (!button) return;
  button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      await auth.logoutAll();
    } catch {
      // Even if the server call fails (already expired, network down…)
      // we still want the local session gone.
    } finally {
      tokenStore.clear();
      window.location.href = `${cfg.uiPath}/login`;
    }
  });
}

async function populateTenantSwitcher() {
  // Superadmin tenant switch. Lets a superadmin "enter" a tenant from the
  // UI: the selected slug rides on every admin request as the tenant header,
  // so the scope-filtered sidebar (Phase A) swaps to that tenant's models and
  // CRUD runs against its schema. Only meaningful in header-resolution
  // multi-tenant mode — in subdomain mode the host already decides the tenant.
  const host = document.getElementById("tenant-switcher");
  if (!host) return;
  if (!cfg.multiTenant || cfg.tenantResolution !== "header") return;

  let tenants = [];
  try {
    const resp = await root.tenants();
    tenants = (resp.items || []).filter((t) => t.is_active);
  } catch (err) {
    // 403/401 → not a superadmin (or not allowed to list tenants). Leave the
    // switcher hidden; regular tenant users are bound to their own tenant.
    if (err instanceof APIError) return;
    return;
  }
  if (tenants.length === 0) return;

  const current = tenantStore.get();
  const select = el(
    "select",
    { id: "tenant-select", class: "tenant-select", "aria-label": "Active tenant" },
    [
      el("option", { value: "", selected: current === "" }, "Global (public)"),
      ...tenants.map((t) =>
        el("option", { value: t.slug, selected: current === t.slug }, t.name || t.slug)
      ),
    ]
  );
  select.addEventListener("change", () => {
    const slug = select.value;
    if (slug) tenantStore.set(slug);
    else tenantStore.clear();
    // Full reload to the dashboard: the contract + sidebar re-fetch in the new
    // context, and the current resource may not exist in the target scope.
    window.location.assign(`${cfg.uiPath}/dashboard`);
  });

  host.replaceChildren(
    el("span", { class: "tenant-switcher-label" }, "Tenant"),
    select
  );
  host.hidden = false;
}

async function populateSidebarNav() {
  const nav = document.getElementById("sidebar-nav");
  if (!nav) return;
  const contract = await getFullContract();
  const models = (contract.models || []).slice().sort((a, b) =>
    a.label_plural.localeCompare(b.label_plural)
  );

  const items = models.map((m) => {
    const link = el("a", { href: `${cfg.uiPath}/${m.resource}` }, m.label_plural);
    if (cfg.resource === m.resource) {
      link.setAttribute("aria-current", "page");
      link.classList.add("active");
    }
    return el("li", {}, link);
  });

  if (items.length === 0) {
    nav.replaceChildren(el("li", {}, el("span", { class: "placeholder" }, "No models")));
    return;
  }
  nav.replaceChildren(...items);
}

async function populateSidebarExtensions() {
  // Phase 9: render extension-contributed nav items in their own list,
  // below the model list. The server has already filtered to items the
  // current principal can see — no client-side permission check needed.
  const list = document.getElementById("sidebar-extensions");
  if (!list) return;
  const { items = [] } = await admin.navigation();
  if (items.length === 0) {
    // No extension items for this principal — leave the section hidden so
    // the sidebar isn't polluted with an empty "Extensions" header.
    return;
  }

  const currentPath = window.location.pathname;
  const header = el(
    "li",
    {},
    el("span", { class: "nav-section-label" }, "Extensions"),
  );

  const links = items.map((it) => {
    const link = el("a", { href: it.path }, it.label);
    if (currentPath === it.path) {
      link.setAttribute("aria-current", "page");
      link.classList.add("active");
    }
    return el("li", {}, link);
  });

  list.replaceChildren(header, ...links);
  list.hidden = false;
}

async function populateUserLine() {
  const slot = document.getElementById("user-ctx");
  if (!slot) return;
  try {
    const me = await auth.me();
    if (me) slot.textContent = me.email || me.full_name || "Signed in";
  } catch {
    // Best-effort only; leave the slot empty so layout doesn't shift.
  }
}

function highlightSettingsLink() {
  // Footer links that map 1:1 to a view; highlight the active one.
  const byView = { settings: "settings-link", permissions: "permissions-link" };
  const id = byView[cfg.view];
  if (!id) return;
  const link = document.getElementById(id);
  if (!link) return;
  link.setAttribute("aria-current", "page");
  link.classList.add("active");
}

main().catch((err) => {
  const message = err instanceof APIError ? err.message : String(err);
  showToast(`Initialization failed: ${message}`, { type: "error" });
});
