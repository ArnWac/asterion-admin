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
function showToast(msg, type = 'info', duration = 3500) {
  let container = document.getElementById('toast-container');
  if (!container) {
    container = document.createElement('div');
    container.id = 'toast-container';
    document.body.appendChild(container);
  }
  const t = document.createElement('div');
  t.className = `toast toast-${type}`;
  t.textContent = msg;
  container.appendChild(t);
  requestAnimationFrame(() => requestAnimationFrame(() => t.classList.add('show')));
  setTimeout(() => {
    t.classList.remove('show');
    t.addEventListener('transitionend', () => t.remove(), { once: true });
  }, duration);
}

function showError(msg) {
  showToast(msg, 'error');
}

function showSuccess(msg) {
  showToast(msg, 'success');
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

  let meta, page = 1, pageSize = Prefs.getPageSize(), orderBy = '';
  const urlQ = new URLSearchParams(window.location.search).get('q') || '';
  let q = urlQ;
  const selected = new Set();

  const actionBar = document.getElementById('action-bar');
  const selectedCount = document.getElementById('selected-count');
  const actionSelect = document.getElementById('action-select');

  function updateActionBar() {
    if (!actionBar) return;
    const hasBulkActions = (meta?.actions || []).some(a => a.bulk);
    if (!hasBulkActions) return;
    if (selected.size === 0) {
      actionBar.style.display = 'none';
    } else {
      actionBar.style.display = 'flex';
      selectedCount.textContent = `${selected.size} selected`;
    }
  }

  function initActionBar() {
    if (!actionBar || !actionSelect) return;
    const bulkActions = (meta?.actions || []).filter(a => a.bulk);
    if (!bulkActions.length) return;
    actionSelect.innerHTML = bulkActions.map(a =>
      `<option value="${esc(a.name)}" data-danger="${a.danger}" data-confirm="${a.confirm}">${esc(a.label)}</option>`
    ).join('');

    document.getElementById('action-run')?.addEventListener('click', async () => {
      if (!selected.size) return;
      const opt = actionSelect.selectedOptions[0];
      if (!opt) return;
      const actionName = opt.value;
      const needsConfirm = opt.dataset.confirm === 'true';
      const isDanger = opt.dataset.danger === 'true';
      if (needsConfirm) {
        const label = opt.textContent;
        const ok = confirm(`Run "${label}" on ${selected.size} item(s)? This cannot be undone.`);
        if (!ok) return;
      }
      try {
        const result = await API.post(`/jobs/admin/${model}/bulk`, {
          action: actionName,
          object_ids: [...selected],
          confirm: needsConfirm,
        });
        showSuccess(result.result_summary || `Action completed — ${result.affected} affected`);
        selected.clear();
        updateActionBar();
        await load();
      } catch (err) {
        showError(fmtAPIError(err));
      }
    });

    document.getElementById('action-clear')?.addEventListener('click', () => {
      selected.clear();
      updateActionBar();
      document.querySelectorAll('.row-check').forEach(cb => cb.checked = false);
      const selAll = document.getElementById('select-all');
      if (selAll) selAll.checked = false;
    });
  }

  async function load() {
    const tableEl = document.getElementById('table-body');
    const theadEl = document.getElementById('table-head');
    const paginEl = document.getElementById('pagination');
    tableEl.innerHTML = '<tr><td colspan="99" class="loading">Loading…</td></tr>';

    let url = `/admin/${model}?page=${page}&page_size=${pageSize}`;
    if (q) url += `&q=${encodeURIComponent(q)}`;
    if (orderBy) url += `&order_by=${encodeURIComponent(orderBy)}`;

    const hasBulkActions = (meta?.actions || []).some(a => a.bulk);

    try {
      const data = await API.get(url);

      // Derive columns from meta list_fields, fallback to item keys
      const cols = meta?.list_fields?.length
        ? meta.list_fields
        : (data.items[0] ? Object.keys(data.items[0]) : []);

      // Header
      const checkboxTh = hasBulkActions
        ? `<th style="width:36px"><input type="checkbox" id="select-all" aria-label="Select all"></th>`
        : '';
      theadEl.innerHTML = '<tr>' + checkboxTh + cols.map(c => {
        const fieldMeta = meta?.fields?.find(f => f.name === c);
        const label = fieldMeta?.label || c;
        const arrow = c === orderBy ? ' ↑' : c === `-${orderBy}`.replace('-','') ? ' ↓' : '';
        return `<th><a href="#" data-sort="${c}" style="color:inherit;text-decoration:none">${esc(label)}${arrow}</a></th>`;
      }).join('') + '<th style="width:120px">Actions</th></tr>';

      // Rows
      const editableCols = new Set(meta?.list_editable || []);
      if (!data.items.length) {
        tableEl.innerHTML = `<tr><td colspan="99" style="color:#586069">No records found.</td></tr>`;
      } else {
        tableEl.innerHTML = data.items.map(item => {
          const id = item.id || '';
          const isChecked = selected.has(id) ? ' checked' : '';
          const checkTd = hasBulkActions
            ? `<td><input type="checkbox" class="row-check" data-id="${id}" aria-label="Select row"${isChecked}></td>`
            : '';
          const cells = cols.map(c => {
            if (editableCols.has(c)) {
              const v = item[c] ?? '';
              return `<td><input class="list-inline-input" data-id="${esc(id)}" data-field="${esc(c)}" value="${esc(String(v))}" aria-label="${esc(c)}"></td>`;
            }
            return `<td>${fmtCell(item[c])}</td>`;
          }).join('');
          const actions = `<td>
            <a class="btn btn-sm btn-secondary" href="${UI_BASE}/${model}/${id}">View</a>
            <a class="btn btn-sm btn-secondary" href="${UI_BASE}/${model}/${id}/edit">Edit</a>
          </td>`;
          return `<tr>${checkTd}${cells}${actions}</tr>`;
        }).join('');

        // Inline-edit save on blur
        if (editableCols.size) {
          tableEl.addEventListener('blur', async e => {
            const inp = e.target.closest('.list-inline-input');
            if (!inp) return;
            const recId = inp.dataset.id;
            const field = inp.dataset.field;
            try {
              await API.patch(`/admin/${model}/${recId}`, { [field]: inp.value });
              inp.style.borderColor = '';
            } catch (err) {
              inp.style.borderColor = '#de350b';
              showError(fmtAPIError(err));
            }
          }, true);
        }
      }

      // Checkbox events (delegated)
      if (hasBulkActions) {
        document.getElementById('select-all')?.addEventListener('change', e => {
          document.querySelectorAll('.row-check').forEach(cb => {
            cb.checked = e.target.checked;
            if (e.target.checked) selected.add(cb.dataset.id);
            else selected.delete(cb.dataset.id);
          });
          updateActionBar();
        });
        tableEl.addEventListener('change', e => {
          const cb = e.target.closest('.row-check');
          if (!cb) return;
          if (cb.checked) selected.add(cb.dataset.id);
          else selected.delete(cb.dataset.id);
          updateActionBar();
        });
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

  initActionBar();

  // Search — pre-fill from URL ?q= if present
  const searchEl = document.getElementById('search-input');
  if (searchEl) {
    if (urlQ) searchEl.value = urlQ;
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
  setBreadcrumb([{ label: 'Home', href: UI_BASE + '/dashboard' }, { label: meta?.label_plural || model, href: '' }]);
  await load();
}

// ---------------------------------------------------------------------------
// Detail page
// ---------------------------------------------------------------------------
async function initDetail(model, objectId) {
  if (!Auth.isLoggedIn()) { Auth.redirectToLogin(); return; }
  const bodyEl = document.getElementById('detail-body');
  bodyEl.innerHTML = '<p class="loading">Loading…</p>';

  let item = null, meta = null;
  try {
    [item, meta] = await Promise.all([
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

    // 4B: permission matrix section
    if (meta?.permission_matrix) {
      const matrixCard = document.createElement('div');
      matrixCard.className = 'card';
      matrixCard.style.marginTop = '1.25rem';
      matrixCard.innerHTML = '<h3 style="font-size:.95rem;font-weight:600;margin-bottom:1rem">CRUD Permissions</h3><div id="permission-matrix-body"></div>';
      const layoutEl = bodyEl.closest('.detail-layout') || bodyEl.parentElement;
      layoutEl.insertAdjacentElement('afterend', matrixCard);
      renderPermissionMatrix(objectId, document.getElementById('permission-matrix-body'));
    }
  } catch (e) {
    bodyEl.innerHTML = `<div class="alert alert-error" style="display:block">${esc(fmtAPIError(e))}</div>`;
  }

  // Change history (right column)
  const historyEl = document.getElementById('detail-history');
  if (historyEl) {
    try {
      const history = await API.get(`/audit?object_id=${encodeURIComponent(objectId)}&page_size=3`);
      if (!history.items.length) {
        historyEl.innerHTML = '<p style="color:#586069;font-size:.82rem">No changes recorded yet.</p>';
      } else {
        const actionColor = { created: 'badge-green', updated: 'badge-gray', deleted: 'badge-red' };
        const truncate = (s, max = 80) => {
          const str = s == null ? '—' : String(s);
          return str.length > max ? str.slice(0, max) + '…' : str;
        };
        const entries = history.items.map(e => {
          const when = new Date(e.created_at).toLocaleString();
          const color = actionColor[e.action] || 'badge-gray';
          const actor = e.actor || 'Unknown';
          let detail = '';
          if (e.changes && Object.keys(e.changes).length) {
            detail = '<div class="history-changes">' +
              Object.entries(e.changes).map(([field, diff]) =>
                `<span class="history-field">${esc(field)}:</span> ` +
                `<span class="history-old">${esc(truncate(diff.from))}</span> → ` +
                `<span class="history-new">${esc(truncate(diff.to))}</span>`
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

        const remaining = history.total - history.items.length;
        const moreLink = remaining > 0
          ? `<a href="${UI_BASE}/audit_logs?q=${encodeURIComponent(objectId)}" style="font-size:.78rem;display:block;margin-top:.6rem;color:var(--link,#0052cc)">+${remaining} more — view all changes →</a>`
          : '';
        historyEl.innerHTML = entries + moreLink;
      }
    } catch (err) {
      console.error('History load failed:', err);
      historyEl.innerHTML = `<p style="color:#586069;font-size:.82rem">History unavailable: ${esc(String(err))}</p>`;
    }
  }

  setBreadcrumb([
    { label: 'Home', href: UI_BASE + '/dashboard' },
    { label: meta?.label_plural || model, href: `${UI_BASE}/${model}` },
    { label: objectId, href: '' },
  ]);
  await initNav(model);
}

// ---------------------------------------------------------------------------
// Create page
// ---------------------------------------------------------------------------
async function initCreate(model) {
  if (!Auth.isLoggedIn()) { Auth.redirectToLogin(); return; }
  const formEl = document.getElementById('record-form');
  let meta = null;

  try {
    meta = await API.get(`/admin/${model}/meta`);
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
      const body = collectForm(formEl, writableFields);
      try {
        const created = await API.post(`/admin/${model}`, body);
        // 1C: respect create_redirect ("list" default, "detail" for opt-in models)
        if (meta.create_redirect === 'detail') {
          window.location.href = `${UI_BASE}/${model}/${created.id}`;
        } else {
          window.location.href = `${UI_BASE}/${model}`;
        }
      } catch (err) {
        showError(fmtAPIError(err));
      }
    });
  } catch (e) {
    formEl.innerHTML =
      `<div class="alert alert-error" style="display:block">${esc(fmtAPIError(e))}</div>`;
  }

  setBreadcrumb([
    { label: 'Home', href: UI_BASE + '/dashboard' },
    { label: meta?.label_plural || model, href: `${UI_BASE}/${model}` },
    { label: 'New', href: '' },
  ]);
  await initNav(model);
}

// ---------------------------------------------------------------------------
// Update page
// ---------------------------------------------------------------------------
async function initUpdate(model, objectId) {
  if (!Auth.isLoggedIn()) { Auth.redirectToLogin(); return; }
  const formEl = document.getElementById('record-form');
  let meta = null, item = null;

  try {
    [meta, item] = await Promise.all([
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
      const writableFields = editableFields.filter(f => !f.readonly);
      const body = collectForm(formEl, writableFields);
      try {
        const updated = await API.patch(`/admin/${model}/${objectId}`, body);
        showSuccess('Saved successfully.');
        editableFields.forEach(f => {
          const el = formEl.querySelector(`[name="${f.name}"]`);
          if (el && updated[f.name] !== undefined) el.value = updated[f.name] ?? '';
        });
      } catch (err) {
        showError(fmtAPIError(err));
      }
    });

    // 4B: permission matrix section below the form
    if (meta?.permission_matrix) {
      const matrixCard = document.createElement('div');
      matrixCard.className = 'card';
      matrixCard.style.marginTop = '1.25rem';
      matrixCard.innerHTML = '<h3 style="font-size:.95rem;font-weight:600;margin-bottom:1rem">CRUD Permissions</h3><div id="permission-matrix-body"></div>';
      formEl.parentElement.insertAdjacentElement('afterend', matrixCard);
      renderPermissionMatrix(objectId, document.getElementById('permission-matrix-body'));
    }
  } catch (e) {
    formEl.innerHTML = `<div class="alert alert-error" style="display:block">${esc(fmtAPIError(e))}</div>`;
  }

  setBreadcrumb([
    { label: 'Home', href: UI_BASE + '/dashboard' },
    { label: meta?.label_plural || model, href: `${UI_BASE}/${model}` },
    { label: objectId, href: `${UI_BASE}/${model}/${objectId}` },
    { label: 'Edit', href: '' },
  ]);
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
  } else if (f.widget === 'choices-select') {
    // 2A: registry/URL-backed select (e.g. model_name → admin registry)
    const currentVal = val ? String(val) : '';
    if (isReadonly) {
      input = `<input type="text" name="${f.name}" id="f_${f.name}" value="${esc(currentVal)}"${roAttr}>`;
    } else {
      input = `<select name="${f.name}" id="f_${f.name}" data-choices-url="${esc(f.choices_url || '')}" data-current-val="${esc(currentVal)}">
        ${currentVal ? `<option value="${esc(currentVal)}" selected>${esc(currentVal)}</option>` : '<option value="">— Loading… —</option>'}
      </select>`;
    }
  } else if (f.widget === 'select-relation') {
    const lookupUrl = f.relation?.lookup_url || '';
    const targetTable = f.relation?.target_table || '';
    const currentVal = val ? String(val) : '';
    // 3B: inline-create modal instead of new-tab link
    const newBtn = targetTable && !isReadonly
      ? `<button type="button" class="btn btn-sm btn-secondary" onclick="AdminUI.openInlineCreate('${esc(targetTable)}','f_${esc(f.name)}')">+ New</button>`
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
  const tasks = [];

  // Standard lookup selects
  formEl.querySelectorAll('select[data-lookup-url]').forEach(sel => {
    tasks.push((async () => {
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
    })());
  });

  // 2A: choices-url selects (admin registry or other URL)
  formEl.querySelectorAll('select[data-choices-url]').forEach(sel => {
    tasks.push((async () => {
      const url = sel.dataset.choicesUrl.replace('/api/v1', '');
      const currentVal = sel.dataset.currentVal || '';
      try {
        const data = await API.get(url);
        // Admin registry returns {models:[{model,label,...}]}, else {items:[{id,label}]}
        let opts;
        if (data.models) {
          opts = data.models.map(m => ({ id: m.model, label: m.label || m.model }));
        } else {
          opts = (data.items || []).map(m => ({ id: m.id, label: m.label || m.id }));
        }
        sel.innerHTML = '<option value="">— Select model —</option>' +
          opts.map(o =>
            `<option value="${esc(o.id)}"${o.id === currentVal ? ' selected' : ''}>${esc(o.label)}</option>`
          ).join('');
      } catch (_) {
        sel.innerHTML = currentVal
          ? `<option value="${esc(currentVal)}" selected>${esc(currentVal)}</option>`
          : '<option value="">— None available —</option>';
      }
    })());
  });

  await Promise.all(tasks);
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
  getPageSize() { return parseInt(this.get().page_size || '20', 10); },
  setPageSize(n) { this.set({page_size: String(n)}); },
  getTheme() { return this.get().theme || 'auto'; },
  setTheme(t) { this.set({theme: t}); },
  applyDensity() {
    const d = this.getDensity();
    if (d !== 'comfortable') document.body.dataset.density = d;
  },
};

// ---------------------------------------------------------------------------
// Settings page
// ---------------------------------------------------------------------------
async function initSettings() {
  if (!Auth.isLoggedIn()) { Auth.redirectToLogin(); return; }
  setBreadcrumb([{ label: 'Settings' }]);
  await initNav(null);

  // --- Profile ---
  const nameEl = document.getElementById('pf-name');
  const emailEl = document.getElementById('pf-email');

  try {
    const me = await API.get('/admin/profile');
    if (nameEl) nameEl.value = me.full_name || '';
    if (emailEl) emailEl.value = me.email || '';
  } catch (e) {
    showError('Failed to load profile.');
  }

  document.getElementById('profile-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const btn = document.getElementById('profile-save-btn');
    btn.disabled = true;

    const body = {};
    const name = nameEl.value.trim();
    const email = emailEl.value.trim();
    const curPw = document.getElementById('pf-cur-pw').value;
    const newPw = document.getElementById('pf-new-pw').value;

    if (name) body.full_name = name;
    if (email) body.email = email;
    if (curPw || newPw) { body.current_password = curPw; body.new_password = newPw; }

    try {
      await API.patch('/admin/profile', body);
      document.getElementById('pf-cur-pw').value = '';
      document.getElementById('pf-new-pw').value = '';
      showSuccess('Profile saved.');
    } catch (err) {
      showError(fmtAPIError(err) || 'Save failed.');
    } finally {
      btn.disabled = false;
    }
  });

  // --- UI Preferences ---
  const densityEl = document.getElementById('pref-density');
  const pageSizeEl = document.getElementById('pref-page-size');
  const themeEl = document.getElementById('pref-theme');

  densityEl.value = Prefs.getDensity();
  pageSizeEl.value = String(Prefs.getPageSize());
  themeEl.value = Prefs.getTheme();

  document.getElementById('prefs-save-btn').addEventListener('click', () => {
    Prefs.setDensity(densityEl.value);
    Prefs.setPageSize(parseInt(pageSizeEl.value, 10));
    Prefs.setTheme(themeEl.value);
    const theme = themeEl.value === 'auto'
      ? (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')
      : themeEl.value;
    DarkMode.apply(theme);
    showToast('Preferences saved.', 'success');
  });
}

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

// ---------------------------------------------------------------------------
// 3B: Inline-create modal for relation fields
// ---------------------------------------------------------------------------
async function openInlineCreate(targetTable, selectId) {
  // Remove any existing modal
  document.getElementById('inline-create-modal')?.remove();

  const overlay = document.createElement('div');
  overlay.id = 'inline-create-modal';
  overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.45);z-index:9000;display:flex;align-items:center;justify-content:center';

  const panel = document.createElement('div');
  panel.style.cssText = 'background:var(--surface,#fff);border-radius:8px;box-shadow:0 8px 32px rgba(0,0,0,.25);padding:1.5rem;width:480px;max-width:95vw;max-height:80vh;overflow-y:auto;position:relative';

  const closeBtn = `<button onclick="document.getElementById('inline-create-modal').remove()" style="position:absolute;top:.75rem;right:.75rem;background:none;border:none;font-size:1.2rem;cursor:pointer;color:var(--text-muted,#586069)" aria-label="Close">✕</button>`;

  panel.innerHTML = closeBtn + `<h3 style="font-size:1rem;margin-bottom:1rem">New ${targetTable}</h3><div id="inline-form-wrap"><p class="loading">Loading…</p></div>`;
  overlay.appendChild(panel);
  document.body.appendChild(overlay);

  // Close on overlay click
  overlay.addEventListener('click', e => { if (e.target === overlay) overlay.remove(); });

  try {
    const meta = await API.get(`/admin/${targetTable}/meta`);
    const wrapEl = document.getElementById('inline-form-wrap');
    const writableFields = (meta.fields || []).filter(
      f => !f.readonly && !['id', 'created_at', 'updated_at'].includes(f.name)
    );

    const form = document.createElement('form');
    form.innerHTML =
      writableFields.map(f => buildFieldInput(f)).join('') +
      '<div style="margin-top:1rem;display:flex;gap:.5rem">' +
        '<button type="submit" class="btn btn-primary">Create</button>' +
        '<button type="button" class="btn btn-secondary" onclick="document.getElementById(\'inline-create-modal\').remove()">Cancel</button>' +
      '</div>';
    wrapEl.innerHTML = '';
    wrapEl.appendChild(form);
    populateRelationSelects(form);

    form.addEventListener('submit', async e => {
      e.preventDefault();
      const body = collectForm(form, writableFields);
      try {
        const created = await API.post(`/admin/${targetTable}`, body);
        const sel = document.getElementById(selectId);
        if (sel) {
          const labelField = meta.list_fields?.[0] || 'id';
          const label = created[labelField] || created.id;
          const opt = new Option(label, created.id, true, true);
          sel.add(opt);
        }
        overlay.remove();
      } catch (err) {
        showError(fmtAPIError(err));
      }
    });
  } catch (err) {
    document.getElementById('inline-form-wrap').innerHTML =
      `<div class="alert alert-error" style="display:block">${esc(fmtAPIError(err))}</div>`;
  }
}

// ---------------------------------------------------------------------------
// 4B: Permission matrix section for role-like models
// ---------------------------------------------------------------------------
async function renderPermissionMatrix(objectId, containerEl) {
  containerEl.innerHTML = '<p class="loading">Loading permissions…</p>';
  try {
    const matrix = await API.get(`/admin/permission-matrix/${objectId}`);

    const ops = ['can_list', 'can_create', 'can_update', 'can_delete'];
    const opLabels = { can_list: 'List', can_create: 'Create', can_update: 'Update', can_delete: 'Delete' };

    const thead = '<thead><tr><th>Model</th>' + ops.map(o => `<th style="text-align:center">${opLabels[o]}</th>`).join('') + '</tr></thead>';
    const tbody = matrix.map(row =>
      `<tr><td style="font-weight:500">${esc(row.model_name)}</td>` +
      ops.map(op =>
        `<td style="text-align:center"><input type="checkbox" data-model="${esc(row.model_name)}" data-op="${esc(op)}"${row[op] ? ' checked' : ''}></td>`
      ).join('') +
      '</tr>'
    ).join('');

    containerEl.innerHTML =
      `<table style="width:100%">${thead}<tbody>${tbody}</tbody></table>` +
      `<button id="matrix-save" class="btn btn-primary" style="margin-top:1rem">Save Permissions</button>`;

    document.getElementById('matrix-save')?.addEventListener('click', async () => {
      const updated = matrix.map(row => {
        const entry = { model_name: row.model_name };
        ops.forEach(op => {
          const cb = containerEl.querySelector(`input[data-model="${row.model_name}"][data-op="${op}"]`);
          entry[op] = cb ? cb.checked : false;
        });
        return entry;
      });
      try {
        await API._fetch(`/admin/permission-matrix/${objectId}`, {
          method: 'PUT', body: JSON.stringify(updated),
        });
        showSuccess('Permissions saved.');
      } catch (err) {
        showError(fmtAPIError(err));
      }
    });
  } catch (err) {
    containerEl.innerHTML = `<div class="alert alert-error" style="display:block">${esc(fmtAPIError(err))}</div>`;
  }
}

// ---------------------------------------------------------------------------
// Breadcrumbs
// ---------------------------------------------------------------------------
function setBreadcrumb(parts) {
  const el = document.getElementById('breadcrumb');
  if (!el) return;
  el.innerHTML = parts.map((p, i) => {
    const isLast = i === parts.length - 1;
    const content = isLast ? `<span>${esc(p.label)}</span>` : `<a href="${esc(p.href)}">${esc(p.label)}</a>`;
    return content + (isLast ? '' : '<span class="bc-sep" aria-hidden="true">›</span>');
  }).join('');
}

// ---------------------------------------------------------------------------
// Dark Mode
// ---------------------------------------------------------------------------
const DarkMode = {
  PREF_KEY: 'coreAdmin_theme',
  apply(theme) {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem(this.PREF_KEY, theme);
    const btn = document.getElementById('dark-mode-btn');
    if (btn) btn.textContent = theme === 'dark' ? '☀ Light' : '☾ Dark';
  },
  init() {
    const saved = localStorage.getItem(this.PREF_KEY);
    const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
    this.apply(saved || (prefersDark ? 'dark' : 'light'));
  },
  toggle() {
    const current = document.documentElement.dataset.theme || 'light';
    this.apply(current === 'dark' ? 'light' : 'dark');
  },
};
DarkMode.init();

// Expose globally
window.AdminUI = { Auth, API, Prefs, DarkMode, initNav, initList, initDetail, initCreate, initUpdate, initConfirmDelete, initBreakGlass, initSettings, openInlineCreate, showToast, showError, showSuccess };
