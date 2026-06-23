// Superadmin "enter tenant" action, shared by the tenant list + detail.
//
// Records a global tenant_access audit event (server-side), sets the active
// tenant to the returned slug, and reloads into the tenant dashboard. The
// superadmin keeps their own rights — this is a scoped context switch, not
// impersonation.

import { APIError, root, tenantStore } from "./api.js";
import { showToast } from "./dom.js";

const cfg = window.ASTERION || {};

export async function openTenant(tenantId) {
  try {
    const tenant = await root.enterTenant(tenantId);
    if (!tenant || !tenant.slug) return;
    if (cfg.tenantResolution === "subdomain") {
      // Subdomain mode: the host decides the tenant, so a header is ignored.
      // Enter the tenant by navigating to its subdomain (prepend the slug to
      // the current apex host); return to global is the bare host. We still
      // recorded the tenant_access audit above via root.enterTenant.
      window.location.assign(subdomainUrl(tenant.slug));
      return;
    }
    // Header mode: stash the slug so attachTenantHeader scopes admin-API
    // requests to this tenant, then reload into the tenant dashboard.
    tenantStore.set(tenant.slug);
    window.location.assign(`${cfg.uiPath}/dashboard`);
  } catch (err) {
    const message = err instanceof APIError ? err.message : String(err);
    showToast(`Could not open tenant: ${message}`, { type: "error" });
  }
}

// Build the dashboard URL on ``<slug>.<current-host>``. The tenants list is
// only reachable from the global (apex) view, so the current hostname is the
// apex — prepending the slug yields the tenant subdomain. Host keeps the port
// (e.g. ``localhost:8000`` → ``acme.localhost:8000``).
function subdomainUrl(slug) {
  const loc = window.location;
  return `${loc.protocol}//${slug}.${loc.host}${cfg.uiPath}/dashboard`;
}
