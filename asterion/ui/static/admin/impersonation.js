// Superadmin impersonation (v0.1.12).
//
// A superadmin starts impersonation from a user's detail page. We mint an
// impersonation token via POST {root}/impersonate, stash the original access
// token, and swap it in; a banner across the shell offers "Stop", which
// restores the original token. The whole feature is gated by
// cfg.enableImpersonation and is superadmin-only (the route rejects
// impersonation tokens, so you can't nest impersonation).

import { auth, root, tenantStore, tokenStore } from "./api.js";
import { el, showToast } from "./dom.js";

const cfg = window.ASTERION || {};
const ORIG_KEY = "asterion_access_orig";
// Backup of the superadmin's active-tenant selection, restored on "Stop" so
// they return to wherever they were before impersonating.
const ORIG_TENANT_KEY = "asterion_tenant_orig";

export function isImpersonating() {
  return !!localStorage.getItem(ORIG_KEY);
}

function gotoDashboard() {
  window.location.assign(`${cfg.uiPath}/dashboard`);
}

/**
 * Build an "Impersonate" button for a user's detail page, or return null
 * when it shouldn't be shown. Async because it confirms the caller is a
 * superadmin (and isn't the target) via /auth/me.
 */
export async function renderImpersonateButton(resource, recordId) {
  if (resource !== "users" || !cfg.enableImpersonation) return null;
  if (isImpersonating()) return null; // can't nest

  let me;
  try {
    me = await auth.me();
  } catch {
    return null;
  }
  if (!me || !me.is_superadmin) return null;
  if (String(me.id) === String(recordId)) return null; // not yourself

  const btn = el("button", { type: "button", class: "btn" }, "Impersonate");
  btn.addEventListener("click", async () => {
    // Governance (G9): the server requires a documented reason by default
    // (impersonation_require_reason). Prompt for one so the bundled UI doesn't
    // dead-end on "A reason is required to impersonate a user".
    const required = cfg.impersonationRequireReason !== false;
    const answer = window.prompt(
      required
        ? "Reason for impersonating this user (required):"
        : "Reason for impersonating this user (optional):",
      "",
    );
    if (answer === null) return; // cancelled — leave the button enabled
    const reason = answer.trim();
    if (required && !reason) {
      showToast("A reason is required to impersonate a user.", { type: "error" });
      return;
    }

    btn.disabled = true;
    try {
      const resp = await root.impersonate(recordId, null, reason || null);
      localStorage.setItem(ORIG_KEY, tokenStore.get() || "");
      localStorage.setItem(ORIG_TENANT_KEY, tenantStore.get() || "");
      tokenStore.set(resp.access_token);
      // Enter the impersonated user's tenant (server resolved it when
      // unambiguous) so we land in their context, not the empty global view.
      if (resp.tenant_slug) tenantStore.set(resp.tenant_slug);
      else tenantStore.clear();
      gotoDashboard();
    } catch (err) {
      btn.disabled = false;
      showToast(err && err.message ? err.message : "Impersonation failed.", { type: "error" });
    }
  });
  return btn;
}

/**
 * Prepend the "you are impersonating" banner to the shell when an
 * impersonation session is active. Safe to call on every page.
 */
export async function renderImpersonationBanner() {
  if (!isImpersonating()) return;

  let label = "another user";
  try {
    const me = await auth.me();
    if (me && me.email) label = me.email;
  } catch {
    // Keep the generic label; the Stop button must still work.
  }

  const stop = el(
    "button",
    { type: "button", class: "btn-impersonation-stop" },
    "Stop impersonating"
  );
  stop.addEventListener("click", () => {
    const orig = localStorage.getItem(ORIG_KEY);
    if (orig) tokenStore.set(orig);
    // Restore the superadmin's prior tenant selection (empty = global).
    const origTenant = localStorage.getItem(ORIG_TENANT_KEY);
    if (origTenant) tenantStore.set(origTenant);
    else tenantStore.clear();
    localStorage.removeItem(ORIG_KEY);
    localStorage.removeItem(ORIG_TENANT_KEY);
    gotoDashboard();
  });

  const bar = el("div", { class: "impersonation-banner", role: "status", "aria-live": "polite" }, [
    el("span", {}, `Impersonating ${label}`),
    stop,
  ]);
  document.body.prepend(bar);
  // Flag the shell so the toast lifts above the fixed bottom banner
  // (see `.is-impersonating .toast` in admin.css) — otherwise an error
  // toast would render behind the orange bar and be unreadable.
  document.body.classList.add("is-impersonating");
}
