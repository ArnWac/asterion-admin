// Entrypoint module.
//
// Both app.html and login.html load this file. We dispatch on
// `body.dataset.view`, dynamically import the matching view module, and
// hand it the `#app-root` element plus any URL-derived arguments the
// server already put into window.ADMINFOUNDRY.
//
// We also wire up the shell-wide concerns: the sign-out button, the
// sidebar resource navigation, the bottom user line, and an
// unauthenticated -> /login redirect for app pages.

import { APIError, auth, tokenStore } from "./api.js";
import { getFullContract } from "./contract.js";
import { el, mount, showToast } from "./dom.js";

const cfg = window.ADMINFOUNDRY || {};

const viewLoaders = {
  login: () => import("./views/login.js").then((m) => m.mountLogin()),
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
};

async function main() {
  const view = document.body.dataset.view || cfg.view;

  if (view === "login") {
    await viewLoaders.login();
    return;
  }

  if (!tokenStore.isLoggedIn()) {
    window.location.href = `${cfg.uiPath}/login`;
    return;
  }

  wireSignout();
  // Sidebar nav + user line are non-essential; failure shouldn't break the view.
  populateSidebarNav().catch(() => {});
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
  if (cfg.view !== "settings") return;
  const link = document.getElementById("settings-link");
  if (!link) return;
  link.setAttribute("aria-current", "page");
  link.classList.add("active");
}

main().catch((err) => {
  const message = err instanceof APIError ? err.message : String(err);
  showToast(`Initialization failed: ${message}`, { type: "error" });
});
