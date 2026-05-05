// coreAdmin built-in UI — shared JS
'use strict';

const API_BASE = '/api/v1';
const TOKEN_KEY = 'coreAdmin_access';
const REFRESH_KEY = 'coreAdmin_refresh';

// ---------------------------------------------------------------------------
// Auth
// ---------------------------------------------------------------------------
const Auth = {
  getToken()      { return localStorage.getItem(TOKEN_KEY); },
  getRefresh()    { return localStorage.getItem(REFRESH_KEY); },
  setTokens(a, r) { localStorage.setItem(TOKEN_KEY, a); if (r) localStorage.setItem(REFRESH_KEY, r); },
  clear()         { localStorage.removeItem(TOKEN_KEY); localStorage.removeItem(REFRESH_KEY); },
  isLoggedIn()    { return !!this.getToken(); },
  redirectToLogin() { window.location.href = UI_BASE + '/login'; },
};

// UI_BASE is injected per-page; fall back to /admin-ui
const UI_BASE = window.ADMIN_UI_BASE || '/admin-ui';

// ---------------------------------------------------------------------------
// API client
// ---------------------------------------------------------------------------
const API = {
  async _fetch(path, opts = {}) {
    const token = Auth.getToken();
    const resp = await fetch(API_BASE + path, {
      ...opts,
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(opts.headers || {}),
      },
    });
    if (resp.status === 401) { Auth.clear(); Auth.redirectToLogin(); throw new Error('Unauthorized'); }
    return resp;
  },
  async get(path) {
    const r = await this._fetch(path);
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  },
  async post(path, body) {
    const r = await this._fetch(path, { method: 'POST', body: JSON.stringify(body) });
    if (!r.ok) { const e = await r.json().catch(() => ({})); throw new APIError(r.status, e); }
    return r.json();
  },
  async patch(path, body) {
    const r = await this._fetch(path, { method: 'PATCH', body: JSON.stringify(body) });
    if (!r.ok) { const e = await r.json().catch(() => ({})); throw new APIError(r.status, e); }
    return r.json();
  },
  async delete(path) {
    const r = await this._fetch(path, { method: 'DELETE' });
    if (!r.ok) throw new Error(await r.text());
  },
};

class APIError extends Error {
  constructor(status, body) {
    super(JSON.stringify(body));
    this.status = status;
    this.body = body;
  }
}

// ---------------------------------------------------------------------------
// Alert helpers
// ---------------------------------------------------------------------------
function showError(msg, el) {
  const e = el || document.getElementById('alert');
  if (!e) return;
  e.className = 'alert alert-error';
  e.textContent = msg;
  e.style.display = 'block';
}

function showSuccess(msg, el) {
  const e = el || document.getElementById('alert');
  if (!e) return;
  e.className = 'alert alert-success';
  e.textContent = msg;
  e.style.display = 'block';
}

function clearAlert(el) {
  const e = el || document.getElementById('alert');
  if (e) { e.style.display = 'none'; e.textContent = ''; }
}

function fmtAPIError(err) {
  if (err instanceof APIError) {
    const d = err.body?.detail;
    if (typeof d === 'string') return d;
    if (d?.errors) return d.errors.map(e => `${e.loc?.join('.')}: ${e.msg}`).join('; ');
    return JSON.stringify(d || err.body);
  }
  return err.message || String(err);
}

// ---------------------------------------------------------------------------
// Sidebar / nav init
// ---------------------------------------------------------------------------
async function initNav(activeModel) {
  const navEl = document.getElementById('nav-items');
  const userEl = document.getElementById('user-ctx');
  if (!navEl && !userEl) return;

  try {
    const [nav, ctx] = await Promise.all([
      API.get('/admin/navigation'),
      API.get('/admin/context'),
    ]);

    if (navEl) {
      navEl.innerHTML = nav.items.map(item => {
        const active = item.model === activeModel ? ' class="active"' : '';
        return `<a href="${UI_BASE}/${item.model}"${active}>${esc(item.label_plural)}</a>`;
      }).join('') || '<span style="opacity:.5;font-size:.8rem">No models registered</span>';
    }

    if (userEl) {
      userEl.textContent = ctx.email || '';
    }

    if (ctx.is_impersonating) {
      const banner = document.createElement('div');
      banner.className = 'impersonation-banner';
      banner.textContent = `Impersonating — token issued by: ${ctx.impersonated_by}`;
      document.body.prepend(banner);
    }

    if (ctx.tenant) {
      const tenantEl = document.getElementById('tenant-ctx');
      if (tenantEl) tenantEl.textContent = `Tenant: ${ctx.tenant.slug}`;
    }
  } catch (e) {
    // nav failure must not break the page
  }
}

// ---------------------------------------------------------------------------
// List page
// ---------------------------------------------------------------------------
async function initList(model) {
  if (!Auth.isLoggedIn()) { Auth.redirectToLogin(); return; }

  let meta, page = 1, pageSize = 20, q = '', orderBy = '';

  async function load() {
    const tableEl = document.getElementById('table-body');
    const theadEl = document.getElementById('table-head');
    const paginEl = document.getElementById('pagination');
    tableEl.innerHTML = '<tr><td colspan="99" class="loading">Loading…</td></tr>';

    let url = `/admin/${model}?page=${page}&page_size=${pageSize}`;
    if (q) url += `&q=${encodeURIComponent(q)}`;
    if (orderBy) url += `&order_by=${encodeURIComponent(orderBy)}`;

    try {
      const data = await API.get(url);

      // Derive columns from meta list_fields, fallback to item keys
      const cols = meta?.list_fields?.length
        ? meta.list_fields
        : (data.items[0] ? Object.keys(data.items[0]) : []);

      // Header
      theadEl.innerHTML = '<tr>' + cols.map(c => {
        const fieldMeta = meta?.fields?.find(f => f.name === c);
        const label = fieldMeta?.label || c;
        const arrow = c === orderBy ? ' ↑' : c === `-${orderBy}`.replace('-','') ? ' ↓' : '';
        return `<th><a href="#" data-sort="${c}" style="color:inherit;text-decoration:none">${esc(label)}${arrow}</a></th>`;
      }).join('') + '<th style="width:120px">Actions</th></tr>';

      // Rows
      if (!data.items.length) {
        tableEl.innerHTML = '<tr><td colspan="99" style="color:#586069">No records found.</td></tr>';
      } else {
        tableEl.innerHTML = data.items.map(item => {
          const cells = cols.map(c => `<td>${fmtCell(item[c])}</td>`).join('');
          const id = item.id || '';
          const actions = `<td>
            <a class="btn btn-sm btn-secondary" href="${UI_BASE}/${model}/${id}">View</a>
            <a class="btn btn-sm btn-secondary" href="${UI_BASE}/${model}/${id}/edit">Edit</a>
          </td>`;
          return `<tr>${cells}${actions}</tr>`;
        }).join('');
      }

      // Pagination
      renderPagination(paginEl, page, data.pages, data.total, (p) => { page = p; load(); });

    } catch (e) {
      tableEl.innerHTML = `<tr><td colspan="99" class="alert-error">${esc(fmtAPIError(e))}</td></tr>`;
    }
  }

  try {
    meta = await API.get(`/admin/${model}/meta`);
    document.getElementById('page-title').textContent = meta.label_plural || model;
  } catch (_) {
    document.getElementById('page-title').textContent = model;
  }

  // Search
  const searchEl = document.getElementById('search-input');
  if (searchEl) {
    searchEl.addEventListener('input', debounce(() => { q = searchEl.value; page = 1; load(); }, 350));
  }

  // Sort clicks (delegated)
  document.addEventListener('click', e => {
    const a = e.target.closest('[data-sort]');
    if (!a) return;
    e.preventDefault();
    const col = a.dataset.sort;
    orderBy = orderBy === col ? `-${col}` : col;
    page = 1; load();
  });

  await initNav(model);
  await load();
}

// ---------------------------------------------------------------------------
// Detail page
// ---------------------------------------------------------------------------
async function initDetail(model, objectId) {
  if (!Auth.isLoggedIn()) { Auth.redirectToLogin(); return; }
  const bodyEl = document.getElementById('detail-body');
  bodyEl.innerHTML = '<p class="loading">Loading…</p>';

  try {
    const [item, meta] = await Promise.all([
      API.get(`/admin/${model}/${objectId}`),
      API.get(`/admin/${model}/meta`).catch(() => null),
    ]);

    const fields = meta?.fields || Object.keys(item).map(k => ({ name: k, label: k }));

    bodyEl.innerHTML = '<div class="detail-grid">' + fields.map(f => {
      const val = item[f.name];
      if (val === undefined) return '';
      return `<div class="detail-label">${esc(f.label || f.name)}</div><div class="detail-value">${fmtCell(val)}</div>`;
    }).join('') + '</div>';

    document.getElementById('page-title').textContent = `${meta?.label || model} detail`;
    const editLink = document.getElementById('edit-link');
    if (editLink) editLink.href = `${UI_BASE}/${model}/${objectId}/edit`;
    const backLink = document.getElementById('back-link');
    if (backLink) backLink.href = `${UI_BASE}/${model}`;
  } catch (e) {
    bodyEl.innerHTML = `<div class="alert alert-error" style="display:block">${esc(fmtAPIError(e))}</div>`;
  }

  // Change history (right column)
  const historyEl = document.getElementById('detail-history');
  if (historyEl) {
    try {
      const history = await API.get(`/audit?object_id=${encodeURIComponent(objectId)}&page_size=15`);
      if (!history.items.length) {
        historyEl.innerHTML = '<p style="color:#586069;font-size:.82rem">No changes recorded yet.</p>';
      } else {
        const actionColor = { created: 'badge-green', updated: 'badge-gray', deleted: 'badge-red' };
        historyEl.innerHTML = history.items.map(e => {
          const when = new Date(e.created_at).toLocaleString();
          const color = actionColor[e.action] || 'badge-gray';
          const actor = e.actor || 'Unknown';
          let detail = '';
          if (e.changes && Object.keys(e.changes).length) {
            detail = '<div class="history-changes">' +
              Object.entries(e.changes).map(([field, diff]) =>
                `<span class="history-field">${esc(field)}:</span> ` +
                `<span class="history-old">${esc(diff.from ?? '—')}</span> → ` +
                `<span class="history-new">${esc(diff.to ?? '—')}</span>`
              ).join('<br>') +
            '</div>';
          }
          return `<div class="history-entry">
            <div style="display:flex;align-items:center;gap:.4rem;flex-wrap:wrap">
              <span class="badge ${color}">${esc(e.action)}</span>
              <span class="history-actor">${esc(actor)}</span>
              <span class="history-time">${esc(when)}</span>
            </div>
            ${detail}
          </div>`;
        }).join('');
      }
    } catch (err) {
      console.error('History load failed:', err);
      historyEl.innerHTML = `<p style="color:#586069;font-size:.82rem">History unavailable: ${esc(String(err))}</p>`;
    }
  }

  await initNav(model);
}

// ---------------------------------------------------------------------------
// Create page
// ---------------------------------------------------------------------------
async function initCreate(model) {
  if (!Auth.isLoggedIn()) { Auth.redirectToLogin(); return; }
  const formEl = document.getElementById('record-form');

  try {
    const meta = await API.get(`/admin/${model}/meta`);
    document.getElementById('page-title').textContent = `New ${meta.label || model}`;
    const backLink = document.getElementById('back-link');
    if (backLink) backLink.href = `${UI_BASE}/${model}`;

    const writableFields = (meta.fields || []).filter(
      f => !f.readonly && !['id', 'created_at', 'updated_at'].includes(f.name)
    );

    if (!writableFields.length) {
      formEl.innerHTML = '<div class="fallback-notice">No editable fields available for this model.</div>';
      return;
    }

    // Rebuild form content — always include the submit button
    formEl.innerHTML =
      writableFields.map(f => buildFieldInput(f)).join('') +
      '<div style="margin-top:1rem"><button type="submit" class="btn btn-primary">Create</button></div>';

    populateRelationSelects(formEl);

    formEl.addEventListener('submit', async e => {
      e.preventDefault();
      clearAlert();
      const body = collectForm(formEl, writableFields);
      try {
        const created = await API.post(`/admin/${model}`, body);
        window.location.href = `${UI_BASE}/${model}/${created.id}`;
      } catch (err) {
        showError(fmtAPIError(err));
      }
    });
  } catch (e) {
    formEl.innerHTML =
      `<div class="alert alert-error" style="display:block">${esc(fmtAPIError(e))}</div>`;
  }

  await initNav(model);
}

// ---------------------------------------------------------------------------
// Update page
// ---------------------------------------------------------------------------
async function initUpdate(model, objectId) {
  if (!Auth.isLoggedIn()) { Auth.redirectToLogin(); return; }
  const formEl = document.getElementById('record-form');

  try {
    const [meta, item] = await Promise.all([
      API.get(`/admin/${model}/meta`),
      API.get(`/admin/${model}/${objectId}`),
    ]);

    document.getElementById('page-title').textContent = `Edit ${meta.label || model}`;
    const backLink = document.getElementById('back-link');
    if (backLink) backLink.href = `${UI_BASE}/${model}/${objectId}`;

    const editableFields = (meta.fields || []).filter(
      f => !['id', 'created_at', 'updated_at'].includes(f.name) && !f.create_only
    );

    if (!editableFields.length) {
      formEl.innerHTML = '<div class="fallback-notice">No editable fields available for this model.</div>';
      return;
    }

    formEl.innerHTML =
      editableFields.map(f => buildFieldInput(f, item[f.name])).join('') +
      '<div style="margin-top:1rem"><button type="submit" class="btn btn-primary">Save</button></div>';

    populateRelationSelects(formEl);

    formEl.addEventListener('submit', async e => {
      e.preventDefault();
      clearAlert();
      const writableFields = editableFields.filter(f => !f.readonly);
      const body = collectForm(formEl, writableFields);
      try {
        const updated = await API.patch(`/admin/${model}/${objectId}`, body);
        showSuccess('Saved successfully.');
        // refresh form values
        editableFields.forEach(f => {
          const el = formEl.querySelector(`[name="${f.name}"]`);
          if (el && updated[f.name] !== undefined) el.value = updated[f.name] ?? '';
        });
      } catch (err) {
        showError(fmtAPIError(err));
      }
    });
  } catch (e) {
    formEl.innerHTML = `<div class="alert alert-error" style="display:block">${esc(fmtAPIError(e))}</div>`;
  }

  await initNav(model);
}

// ---------------------------------------------------------------------------
// Form field builder
// ---------------------------------------------------------------------------
function buildFieldInput(f, value) {
  const isReadonly = f.readonly;
  const req = f.required && !f.has_default && !isReadonly ? '<span class="req">*</span>' : '';
  const roAttr = isReadonly ? ' readonly' : '';
  const val = value !== undefined && value !== null ? value : '';

  let input;
  if (f.field_type === 'boolean') {
    const checked = val === true || val === 'true' || val === 1 ? ' checked' : '';
    input = `<input type="checkbox" name="${f.name}" id="f_${f.name}"${checked}${roAttr}>`;
  } else if (f.widget === 'password') {
    input = `<input type="password" name="${f.name}" id="f_${f.name}" autocomplete="new-password"${roAttr}>`;
  } else if (f.widget === 'select-relation') {
    const lookupUrl = f.relation?.lookup_url || '';
    const targetTable = f.relation?.target_table || '';
    const currentVal = val ? String(val) : '';
    const newBtn = targetTable
      ? `<a href="${UI_BASE}/${targetTable}/new" target="_blank" class="btn btn-sm btn-secondary">+ New</a>`
      : '';
    if (isReadonly || !lookupUrl) {
      input = `<input type="text" name="${f.name}" id="f_${f.name}" value="${esc(currentVal)}"${roAttr} placeholder="UUID">`;
    } else {
      input = `<div class="relation-field">
        <select name="${f.name}" id="f_${f.name}" data-lookup-url="${esc(lookupUrl)}" data-current-val="${esc(currentVal)}">
          ${currentVal ? `<option value="${esc(currentVal)}" selected>Loading…</option>` : '<option value="">— Loading… —</option>'}
        </select>
        ${newBtn}
      </div>`;
    }
  } else {
    const itype = fieldInputType(f.field_type);
    input = `<input type="${itype}" name="${f.name}" id="f_${f.name}" value="${esc(String(val))}"${roAttr}>`;
  }

  const hint = isReadonly ? '<p class="field-hint">Read-only</p>' : '';
  return `<div class="form-group">
    <label for="f_${f.name}">${esc(f.label || f.name)}${req}</label>
    ${input}${hint}
  </div>`;
}

function fieldInputType(ft) {
  return { integer: 'number', float: 'number', datetime: 'datetime-local', uuid: 'text' }[ft] || 'text';
}

function collectForm(formEl, fields) {
  const body = {};
  fields.forEach(f => {
    const el = formEl.querySelector(`[name="${f.name}"]`);
    if (!el) return;
    if (f.field_type === 'boolean') {
      body[f.name] = el.checked;
    } else if (f.field_type === 'integer') {
      if (el.value !== '') body[f.name] = parseInt(el.value, 10);
    } else if (f.field_type === 'float') {
      if (el.value !== '') body[f.name] = parseFloat(el.value);
    } else {
      if (el.value !== '') body[f.name] = el.value;
    }
  });
  return body;
}

// ---------------------------------------------------------------------------
// Relation select population
// ---------------------------------------------------------------------------
async function populateRelationSelects(formEl) {
  const selects = formEl.querySelectorAll('select[data-lookup-url]');
  await Promise.all(Array.from(selects).map(async sel => {
    const lookupUrl = sel.dataset.lookupUrl.replace('/api/v1', '');
    const currentVal = sel.dataset.currentVal || '';
    try {
      const data = await API.get(lookupUrl + '?page_size=100');
      sel.innerHTML = '<option value="">— Select —</option>' +
        data.items.map(item =>
          `<option value="${esc(item.id)}"${item.id === currentVal ? ' selected' : ''}>${esc(item.label)}</option>`
        ).join('');
    } catch (_) {
      sel.innerHTML = currentVal
        ? `<option value="${esc(currentVal)}" selected>${esc(currentVal)}</option>`
        : '<option value="">— None available —</option>';
    }
  }));
}

// ---------------------------------------------------------------------------
// Pagination renderer
// ---------------------------------------------------------------------------
function renderPagination(el, currentPage, totalPages, totalItems, onPage) {
  if (!el) return;
  if (totalPages <= 1) { el.innerHTML = `<span>${totalItems} item${totalItems !== 1 ? 's' : ''}</span>`; return; }

  const prev = `<button ${currentPage <= 1 ? 'disabled' : ''} data-p="${currentPage - 1}">‹</button>`;
  const next = `<button ${currentPage >= totalPages ? 'disabled' : ''} data-p="${currentPage + 1}">›</button>`;
  const pages = Array.from({ length: totalPages }, (_, i) => i + 1)
    .filter(p => Math.abs(p - currentPage) <= 2 || p === 1 || p === totalPages)
    .reduce((acc, p, i, arr) => {
      if (i > 0 && p - arr[i - 1] > 1) acc.push('…');
      acc.push(p); return acc;
    }, [])
    .map(p => typeof p === 'string'
      ? `<span style="padding:0 .25rem">${p}</span>`
      : `<button class="${p === currentPage ? 'active' : ''}" data-p="${p}">${p}</button>`)
    .join('');

  el.innerHTML = prev + pages + next + `<span style="margin-left:.5rem;color:#586069">${totalItems} total</span>`;
  el.querySelectorAll('button[data-p]').forEach(btn => {
    btn.addEventListener('click', () => onPage(+btn.dataset.p));
  });
}

// ---------------------------------------------------------------------------
// Utilities
// ---------------------------------------------------------------------------
function esc(s) {
  if (s == null) return '';
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function fmtCell(val) {
  if (val === null || val === undefined) return '<span style="color:#aaa">—</span>';
  if (typeof val === 'boolean') {
    return val
      ? '<span class="badge badge-green">Yes</span>'
      : '<span class="badge badge-red">No</span>';
  }
  return esc(String(val));
}

function debounce(fn, ms) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

// ---------------------------------------------------------------------------
// Personal UI preferences (localStorage-backed, non-security)
// ---------------------------------------------------------------------------
const PREFS_KEY = 'coreAdmin_prefs';
const Prefs = {
  get() {
    try { return JSON.parse(localStorage.getItem(PREFS_KEY) || '{}'); }
    catch { return {}; }
  },
  set(data) { localStorage.setItem(PREFS_KEY, JSON.stringify({...this.get(), ...data})); },
  getDensity() { return this.get().density || 'comfortable'; },
  setDensity(d) { this.set({density: d}); document.body.dataset.density = d; },
  getColumns(model) { return (this.get().visible_columns || {})[model]; },
  setColumns(model, cols) {
    const p = this.get();
    p.visible_columns = {...(p.visible_columns || {}), [model]: cols};
    this.set(p);
  },
  getSorting(model) { return (this.get().sorting || {})[model]; },
  setSorting(model, col) {
    const p = this.get();
    p.sorting = {...(p.sorting || {}), [model]: col};
    this.set(p);
  },
  getFavorites() { return this.get().navigation_favorites || []; },
  setFavorites(list) { this.set({navigation_favorites: list}); },
  applyDensity() {
    const d = this.getDensity();
    if (d !== 'comfortable') document.body.dataset.density = d;
  },
};

// ---------------------------------------------------------------------------
// Confirm delete page
// ---------------------------------------------------------------------------
async function initConfirmDelete(model, objectId) {
  if (!Auth.isLoggedIn()) { Auth.redirectToLogin(); return; }
  const bodyEl = document.getElementById('detail-body');
  const backLink = document.getElementById('back-link');
  if (backLink) backLink.href = `${UI_BASE}/${model}/${objectId}`;

  try {
    const [item, meta] = await Promise.all([
      API.get(`/admin/${model}/${objectId}`),
      API.get(`/admin/${model}/meta`).catch(() => null),
    ]);

    const fields = meta?.fields || Object.keys(item).map(k => ({ name: k, label: k }));
    bodyEl.innerHTML = '<div class="detail-grid">' + fields.map(f => {
      const val = item[f.name];
      if (val === undefined) return '';
      return `<div class="detail-label">${esc(f.label || f.name)}</div><div class="detail-value">${fmtCell(val)}</div>`;
    }).join('') + '</div>';

    document.getElementById('page-title').textContent = `Delete ${meta?.label || model}`;
  } catch (e) {
    bodyEl.innerHTML = `<div class="alert alert-error" style="display:block">${esc(fmtAPIError(e))}</div>`;
  }

  const btn = document.getElementById('confirm-delete-btn');
  if (btn) {
    btn.addEventListener('click', async () => {
      btn.disabled = true;
      btn.textContent = 'Deleting…';
      clearAlert();
      try {
        await API.delete(`/admin/${model}/${objectId}`);
        window.location.href = `${UI_BASE}/${model}`;
      } catch (e) {
        showError(fmtAPIError(e));
        btn.disabled = false;
        btn.textContent = 'Confirm Delete';
      }
    });
  }

  await initNav(model);
}

// ---------------------------------------------------------------------------
// Break-glass initiation page
// ---------------------------------------------------------------------------
async function initBreakGlass(model, objectId) {
  if (!Auth.isLoggedIn()) { Auth.redirectToLogin(); return; }
  const formEl = document.getElementById('bg-form');
  const fieldsEl = document.getElementById('bg-fields');
  const backLink = document.getElementById('back-link');
  if (backLink) backLink.href = `${UI_BASE}/${model}/${objectId}`;

  try {
    const meta = await API.get(`/admin/${model}/meta`);
    document.getElementById('page-title').textContent = `Break-glass Edit: ${meta.label || model}`;

    // Only show non-protected, non-readonly fields as editable targets
    const editableFields = (meta.fields || []).filter(
      f => !f.readonly && !['id', 'created_at', 'updated_at'].includes(f.name)
    );

    if (!editableFields.length) {
      fieldsEl.innerHTML = '<div class="fallback-notice">No writable fields available for break-glass editing.</div>';
    } else {
      fieldsEl.innerHTML = '<p style="font-size:.85rem;color:#586069;margin-bottom:.75rem">Select fields to change:</p>' +
        editableFields.map(f => buildFieldInput(f)).join('');
    }

    formEl.addEventListener('submit', async e => {
      e.preventDefault();
      clearAlert();
      const reasonEl = document.getElementById('bg-reason');
      const reasonErrEl = document.getElementById('reason-error');
      const reason = reasonEl ? reasonEl.value.trim() : '';

      if (reason.length < 10) {
        if (reasonErrEl) { reasonErrEl.textContent = 'Reason must be at least 10 characters.'; reasonErrEl.style.display = 'block'; }
        reasonEl && reasonEl.focus();
        return;
      }
      if (reasonErrEl) reasonErrEl.style.display = 'none';

      const changes = {};
      editableFields.forEach(f => {
        const el = formEl.querySelector(`[name="${f.name}"]`);
        if (!el || el.value === '') return;
        if (f.field_type === 'boolean') changes[f.name] = el.checked;
        else if (f.field_type === 'integer') changes[f.name] = parseInt(el.value, 10);
        else if (f.field_type === 'float') changes[f.name] = parseFloat(el.value);
        else changes[f.name] = el.value;
      });

      if (!Object.keys(changes).length) {
        showError('No fields to change. Fill in at least one field.');
        return;
      }

      try {
        await API.post(`/break-glass/${model}/${objectId}`, { reason, changes });
        window.location.href = `${UI_BASE}/${model}/${objectId}`;
      } catch (err) {
        showError(fmtAPIError(err));
      }
    });
  } catch (e) {
    fieldsEl.innerHTML = `<div class="alert alert-error" style="display:block">${esc(fmtAPIError(e))}</div>`;
  }

  await initNav(model);
}

// Expose globally
window.AdminUI = { Auth, API, Prefs, initNav, initList, initDetail, initCreate, initUpdate, initConfirmDelete, initBreakGlass, showError, showSuccess };
