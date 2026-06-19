// /admin/login-complete — landing page for the OAuth fragment redirect.
//
// The OAuth callback ends with a 302 to:
//   /admin/login-complete#token=<jwt>&refresh=<jwt>&return_to=<relative path>
//
// (Roadmap 3.5: refresh is the long-lived refresh token added alongside
// the access token — pre-3.5 only token was present.)
//
// We read the fragment, store both tokens under the localStorage keys
// the rest of the UI uses, replace the URL so the fragment is gone (so
// a copied URL never leaks the JWTs), and navigate to return_to.
//
// Safety: every failure path lands on /admin/login with an error code
// so the user gets a clear "try again" message rather than a blank page.

import { tokenStore } from "../api.js";

const cfg = window.ASTERION || {};

function _parseFragment(hash) {
  // location.hash includes the leading '#'.
  const raw = (hash || "").replace(/^#/, "");
  const params = new URLSearchParams(raw);
  return {
    token: params.get("token"),
    refresh: params.get("refresh"),
    return_to: params.get("return_to"),
  };
}

function _isSafeReturnTo(value) {
  // Mirror the server-side open-redirect guard in router.py:
  // only same-site relative paths allowed. Anything starting with
  // '//' is a scheme-relative URL that points off-site.
  if (typeof value !== "string" || value.length === 0) return false;
  if (!value.startsWith("/")) return false;
  if (value.startsWith("//")) return false;
  return true;
}

function _bail(code) {
  // Single point of failure handling — drop the fragment and bounce
  // to the login page with a generic error code the login view knows
  // how to render.
  window.location.replace(`${cfg.uiPath}/login?oauth_error=${encodeURIComponent(code)}`);
}

export function mountLoginComplete() {
  const status = document.getElementById("login-complete-status");
  const errorBox = document.getElementById("login-complete-error");

  const { token, refresh, return_to } = _parseFragment(window.location.hash);

  if (!token) {
    // Someone hit the page directly, or the fragment got stripped by
    // an intermediary. Either way, no token, no login.
    _bail("missing_token");
    return;
  }

  try {
    tokenStore.set(token);
    // Refresh is optional — pre-3.5 callbacks didn't ship one, so
    // missing refresh is not a failure (the access token still works
    // until expiry; the UI just can't silently re-acquire).
    if (refresh && typeof tokenStore.setRefresh === "function") {
      tokenStore.setRefresh(refresh);
    }
  } catch (err) {
    if (errorBox) {
      errorBox.textContent = "Could not store the session token.";
      errorBox.hidden = false;
    }
    _bail("storage_failed");
    return;
  }

  // Replace the URL so the fragment is gone before any further
  // navigation — protects against copy-paste leaks of the JWT.
  try {
    window.history.replaceState(null, "", `${cfg.uiPath}/login-complete`);
  } catch {
    // Old browsers without replaceState — not worth special-casing in
    // 2026; the redirect below still happens.
  }

  const dest = _isSafeReturnTo(return_to) ? return_to : `${cfg.uiPath}/dashboard`;
  if (status) {
    status.textContent = "Signed in. Redirecting…";
  }
  window.location.replace(dest);
}
