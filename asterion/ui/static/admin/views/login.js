// Login form. Lives on /admin/login (login.html), not inside app.html.
//
// Two responsibilities beyond the password form:
//
// 1. If the URL has ?oauth_error=<code>, render a friendly message —
//    every OAuth callback failure (state mismatch, expired cookie,
//    unverified email, etc.) lands here so users see ONE place to
//    interpret the failure.
// 2. Fetch /api/v1/admin/_login_contract (anonymous-readable) and
//    render a button per configured OAuth provider. Each button is a
//    plain anchor to /api/v1/oauth/{id}/login — clicking it starts
//    the redirect flow.

import { APIError, auth, tokenStore } from "../api.js";

const cfg = window.ASTERION || {};

// Map the small set of error codes the OAuth callback emits to
// user-friendly messages. Unknown codes get a generic fallback so
// future server-side codes don't break the UI.
const OAUTH_ERROR_MESSAGES = {
  state_invalid: "Your sign-in session expired. Please try again.",
  state_mismatch: "Sign-in could not be verified. Please try again.",
  idp_error: "The identity provider refused the sign-in.",
  missing_code: "Sign-in did not complete. Please try again.",
  token_exchange_failed: "Could not complete sign-in with the provider.",
  id_token_invalid: "The identity provider's response could not be verified.",
  claims_invalid: "The identity provider returned an unexpected response.",
  user_resolve_failed:
    "Sign-in succeeded with the provider, but no matching account exists here.",
  missing_token: "Sign-in did not complete. Please try again.",
  storage_failed: "Could not save your session. Check browser storage settings.",
  internal: "An internal error occurred. Please try again.",
};

export async function mountLogin() {
  if (tokenStore.isLoggedIn()) {
    window.location.href = `${cfg.uiPath}/dashboard`;
    return;
  }
  const form = document.getElementById("login-form");
  const errorBox = document.getElementById("login-error");
  if (!form) return;

  _renderOAuthError(errorBox);

  // Fire the OAuth-provider fetch in parallel with form wiring — both
  // are independent. Failure is non-fatal; the password form still
  // works without the buttons.
  _renderOAuthProviders().catch(() => {});

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    errorBox.hidden = true;
    const data = new FormData(form);
    try {
      const res = await auth.login(data.get("email"), data.get("password"));
      tokenStore.set(res.access_token);
      window.location.href = `${cfg.uiPath}/dashboard`;
    } catch (err) {
      const message = err instanceof APIError ? err.message : "Sign in failed.";
      errorBox.textContent = message;
      errorBox.hidden = false;
    }
  });
}

function _renderOAuthError(errorBox) {
  if (!errorBox) return;
  const params = new URLSearchParams(window.location.search);
  const code = params.get("oauth_error");
  if (!code) return;
  const message =
    OAUTH_ERROR_MESSAGES[code] || "Sign-in did not complete. Please try again.";
  errorBox.textContent = message;
  errorBox.hidden = false;
}

async function _renderOAuthProviders() {
  const slot = document.getElementById("oauth-providers");
  if (!slot) return;

  // Anonymous endpoint — no Authorization header needed. We fetch
  // directly via fetch() rather than going through api.js's `admin`
  // helper because that one auto-redirects on 401, which is wrong
  // for an explicitly-public endpoint.
  const resp = await fetch(`${cfg.adminPrefix}/_login_contract`, {
    headers: { Accept: "application/json" },
  });
  if (!resp.ok) return;
  const payload = await resp.json();
  const providers = Array.isArray(payload?.oauth_providers)
    ? payload.oauth_providers
    : [];
  if (providers.length === 0) {
    // Nothing to render — leave the slot empty.
    return;
  }

  // The "Or sign in with" divider only renders when there are real
  // buttons below it. Hiding when empty keeps the bare-password layout
  // unchanged for apps that don't ship OAuth.
  const divider = document.createElement("div");
  divider.className = "oauth-divider";
  divider.textContent = "Or sign in with";

  const buttons = providers
    .filter((p) => p && p.id && p.login_url && p.label)
    .map((p) => {
      const a = document.createElement("a");
      a.className = "btn btn-oauth";
      a.href = p.login_url;
      a.textContent = p.label;
      a.dataset.provider = p.id;
      return a;
    });

  if (buttons.length === 0) return;

  slot.appendChild(divider);
  for (const b of buttons) slot.appendChild(b);
  slot.hidden = false;
}
