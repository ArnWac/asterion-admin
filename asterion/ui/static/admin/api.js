// HTTP client around fetch().
//
// Speaks the asterion envelope:
//   { "error": { "code", "message", "fields?", "request_id?" } }
//
// Resolves to parsed JSON on 2xx. Rejects with APIError on 4xx/5xx.
// Auto-redirects to /login on 401.

const cfg = window.ASTERION || {};
const TOKEN_KEY = "asterion_access";
const REFRESH_KEY = "asterion_refresh";
const TENANT_KEY = "asterion_tenant";

// Superadmin tenant switch (header resolution only). The selected slug is
// attached as the configured tenant header to ADMIN-prefix requests so a
// superadmin can browse a tenant's schema from the UI. Persisted in
// localStorage so the choice survives the full-page reload the switcher
// triggers.
export const tenantStore = {
  get: () => localStorage.getItem(TENANT_KEY) || "",
  set: (slug) => localStorage.setItem(TENANT_KEY, slug),
  clear: () => localStorage.removeItem(TENANT_KEY),
};

function attachTenantHeader(headers, path) {
  // Header-mode multi-tenancy only — in subdomain mode the host decides the
  // tenant, so a header would be ignored. Scope strictly to admin-API paths:
  // auth/ and root/ routes never carry it, so a stale selection can't break
  // login or the switcher's own /root/tenants lookup.
  if (!cfg.multiTenant || cfg.tenantResolution !== "header") return;
  if (!cfg.adminPrefix || !path.startsWith(cfg.adminPrefix)) return;
  const slug = tenantStore.get();
  if (slug) headers[cfg.tenantHeaderName || "X-Tenant-Slug"] = slug;
}

export const tokenStore = {
  get: () => localStorage.getItem(TOKEN_KEY),
  set: (token) => localStorage.setItem(TOKEN_KEY, token),
  clear: () => {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(REFRESH_KEY);
  },
  isLoggedIn: () => !!localStorage.getItem(TOKEN_KEY),
  // Roadmap 3.1/3.5 — refresh-token plumbing. ``setRefresh`` is the
  // login-flow hook; ``getRefresh`` is the API client's hook for a
  // future silent-refresh on 401. Optional everywhere — pre-3.5 login
  // flows that don't supply a refresh token still work.
  setRefresh: (token) => localStorage.setItem(REFRESH_KEY, token),
  getRefresh: () => localStorage.getItem(REFRESH_KEY),
};

export class APIError extends Error {
  constructor(status, payload) {
    const env = (payload && payload.error) || {};
    super(env.message || `HTTP ${status}`);
    this.status = status;
    this.code = env.code || `http_${status}`;
    this.fields = Array.isArray(env.fields) ? env.fields : [];
    this.requestId = env.request_id || null;
    this.envelope = env;
  }
  fieldErrors() {
    const map = {};
    for (const f of this.fields) {
      if (f && f.name) map[f.name] = f.message || "Invalid value.";
    }
    return map;
  }
}

export function redirectToLogin() {
  tokenStore.clear();
  if (window.ASTERION?.view !== "login") {
    window.location.href = `${cfg.uiPath}/login`;
  }
}

async function request(method, path, body, opts = {}) {
  const headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
  const token = tokenStore.get();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  attachTenantHeader(headers, path);

  const init = { method, headers };
  if (body !== undefined) init.body = JSON.stringify(body);

  const resp = await fetch(path, init);

  if (resp.status === 401 && !opts.skipAuthRedirect) {
    redirectToLogin();
    throw new APIError(401, await safeJson(resp));
  }

  if (resp.status === 204) return null;

  const payload = await safeJson(resp);
  if (!resp.ok) throw new APIError(resp.status, payload);
  return payload;
}

async function safeJson(resp) {
  try {
    return await resp.json();
  } catch {
    return null;
  }
}

// --- public API ---

export const auth = {
  login: (email, password) =>
    request("POST", `${cfg.authPrefix}/login`, { email, password }, { skipAuthRedirect: true }),
  me: () => request("GET", `${cfg.authPrefix}/me`),
  logoutAll: () => request("POST", `${cfg.authPrefix}/logout-all`),
};

export const admin = {
  contract: () => request("GET", `${cfg.adminPrefix}/_contract`),
  contractFor: (resource) => request("GET", `${cfg.adminPrefix}/_contract/${resource}`),
  // Per-user navigation: only items the principal has permission to use.
  // Returns { items: [{id, label, path}, ...] }. Extension-contributed.
  navigation: () => request("GET", `${cfg.adminPrefix}/_navigation`),

  list: (resource, { limit = 25, offset = 0, search = "", ordering = "", dh = "" } = {}) => {
    const qs = new URLSearchParams({ limit, offset });
    if (search) qs.set("search", search);
    if (ordering) qs.set("ordering", ordering);
    if (dh) qs.set("dh", dh);
    return request("GET", `${cfg.adminPrefix}/${resource}?${qs}`);
  },
  read: (resource, id) => request("GET", `${cfg.adminPrefix}/${resource}/${encodeURIComponent(id)}`),
  create: (resource, payload) => request("POST", `${cfg.adminPrefix}/${resource}`, payload),
  update: (resource, id, payload) =>
    request("PATCH", `${cfg.adminPrefix}/${resource}/${encodeURIComponent(id)}`, payload),
  remove: (resource, id) =>
    request("DELETE", `${cfg.adminPrefix}/${resource}/${encodeURIComponent(id)}`),

  // Foreign-key picker options: {value, label} pairs for a FK column,
  // resolved server-side from the column's target table. Returns
  //   { options: [{value, label}, ...] }
  fieldOptions: (resource, field, { q = "", limit = 100 } = {}) => {
    const qs = new URLSearchParams({ limit });
    if (q) qs.set("q", q);
    return request(
      "GET",
      `${cfg.adminPrefix}/${resource}/_options/${encodeURIComponent(field)}?${qs}`
    );
  },

  // Dual-list (Theme F) inline option source: {value, label} pairs for an
  // inline's assignment column (e.g. all permission keys, all tenant members).
  // Returns { options: [{value, label}, ...] }.
  inlineOptions: (resource, inline, { q = "", limit = 1000 } = {}) => {
    const qs = new URLSearchParams({ limit });
    if (q) qs.set("q", q);
    return request(
      "GET",
      `${cfg.adminPrefix}/${resource}/_inline_options/${encodeURIComponent(inline)}?${qs}`
    );
  },

  // Bulk action over a row selection. ``data`` carries the typed input for
  // actions that declare an ``input_schema`` (omitted otherwise).
  runAction: (resource, action, ids, data = undefined) =>
    request("POST", `${cfg.adminPrefix}/${resource}/_actions/${action}`, { ids, data }),

  // Single-row action: the record id is in the URL, so the body carries only
  // the optional typed ``data``. Used by the per-row action icons (bulk=false).
  runRowAction: (resource, id, action, data = undefined) =>
    request(
      "POST",
      `${cfg.adminPrefix}/${resource}/${encodeURIComponent(id)}/_actions/${action}`,
      { data }
    ),

  // import_export extension. Returns a Blob and triggers a browser download.
  // If ``ids`` is non-empty, the server returns ONLY those primary keys and
  // ignores ``search``; otherwise it streams the full (search-filtered) list.
  exportDownload: (resource, { format = "csv", search = "", ids = [] } = {}) => {
    const qs = new URLSearchParams({ format });
    if (ids && ids.length > 0) {
      for (const id of ids) qs.append("ids", String(id));
    } else if (search) {
      qs.set("search", search);
    }
    const url = `${cfg.adminPrefix}/${resource}/_export?${qs}`;
    return downloadFile(url);
  },
  // import_export extension. Returns the JSON import report.
  importFile: (resource, file) =>
    uploadFile(`${cfg.adminPrefix}/${resource}/_import`, file),

  // Permission matrix (Roadmap 5.2). Returns
  //   { roles: [...], permissions: [...], assignments: {role_id: [keys]} }
  // and accepts the same ``assignments`` shape on save.
  permissionMatrix: () =>
    request("GET", `${cfg.adminPrefix}/_permission_matrix`),
  permissionMatrixSave: (assignments) =>
    request("PUT", `${cfg.adminPrefix}/_permission_matrix`, { assignments }),
};

// Superadmin-only root endpoints. ``tenants`` powers the tenant switcher;
// it returns 403 for non-superadmins, which the switcher treats as "hide".
export const root = {
  tenants: () => request("GET", `${cfg.rootPrefix}/tenants?limit=100`),
  impersonate: (targetUserId, tenantId = null) =>
    request("POST", `${cfg.rootPrefix}/impersonate`, {
      target_user_id: targetUserId,
      tenant_id: tenantId,
    }),
  // Records a superadmin entering a tenant (global tenant_access audit) and
  // returns the tenant (incl. slug) so the caller can set the active tenant.
  enterTenant: (tenantId) =>
    request("POST", `${cfg.rootPrefix}/tenants/${encodeURIComponent(tenantId)}/access`),
};


// --- file helpers (used by the import_export extension UI) ---

async function downloadFile(url) {
  const headers = {};
  const token = tokenStore.get();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  attachTenantHeader(headers, url);

  const resp = await fetch(url, { headers });
  if (resp.status === 401) {
    redirectToLogin();
    throw new APIError(401, await safeJson(resp));
  }
  if (!resp.ok) {
    throw new APIError(resp.status, await safeJson(resp));
  }
  const blob = await resp.blob();
  const cd = resp.headers.get("content-disposition") || "";
  const match = /filename="([^"]+)"/.exec(cd);
  const filename = (match && match[1]) || "download";

  const objectUrl = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = objectUrl;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
}

async function uploadFile(url, file) {
  const headers = {};
  const token = tokenStore.get();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  attachTenantHeader(headers, url);

  const form = new FormData();
  form.append("file", file, file.name);

  const resp = await fetch(url, { method: "POST", headers, body: form });
  if (resp.status === 401) {
    redirectToLogin();
    throw new APIError(401, await safeJson(resp));
  }
  if (resp.status === 204) return null;
  const payload = await safeJson(resp);
  if (!resp.ok) throw new APIError(resp.status, payload);
  return payload;
}
