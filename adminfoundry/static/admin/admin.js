// adminfoundry built-in UI — shared JS
'use strict';

const API_BASE = '/api/v1';
const TOKEN_KEY = 'adminfoundry_access';
const REFRESH_KEY = 'adminfoundry_refresh';

let _adminCtx = null; // cached from initNav; read by initDetail for impersonation checks

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
// i18n — T(key, vars) looks up the active language, falls back to 'en', then key
// ---------------------------------------------------------------------------
function T(key, vars) {
  const lang = (typeof Prefs !== 'undefined' && Prefs.getLanguage?.())
    || window.ADMIN_TENANT_LOCALE?.language
    || window.ADMIN_LOCALE_DEFAULTS?.language
    || 'en';
  const i18n = window.ADMIN_I18N || {};
  const catalog = i18n[lang] || i18n['en'] || {};
  let s = Object.prototype.hasOwnProperty.call(catalog, key) ? catalog[key] : key;
  if (vars) Object.entries(vars).forEach(([k, v]) => { s = s.replaceAll(`{${k}}`, String(v)); });
  return s;
}

// Apply data-i18n / data-i18n-placeholder / data-i18n-aria attributes to static template HTML
function applyI18n() {
  document.querySelectorAll('[data-i18n]').forEach(el => { el.textContent = T(el.dataset.i18n); });
  document.querySelectorAll('[data-i18n-placeholder]').forEach(el => { el.placeholder = T(el.dataset.i18nPlaceholder); });
  document.querySelectorAll('[data-i18n-aria]').forEach(el => { el.setAttribute('aria-label', T(el.dataset.i18nAria)); });
}

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
    _adminCtx = ctx;

    if (navEl) {
      navEl.innerHTML = nav.items.map(item => {
        const active = item.model === activeModel ? ' class="active"' : '';
        return `<a href="${UI_BASE}/${item.model}"${active}>${esc(item.label_plural)}</a>`;
      }).join('') || `<span style="opacity:.5;font-size:.8rem">${T('text_no_models')}</span>`;
    }

    if (userEl) {
      userEl.textContent = ctx.email || '';
    }

    if (ctx.is_impersonating) {
      const banner = document.createElement('div');
      banner.className = 'impersonation-banner';
      banner.style.display = 'flex';
      banner.style.alignItems = 'center';
      banner.style.justifyContent = 'space-between';
      const msg = document.createElement('span');
      msg.textContent = T('label_impersonating', {by: ctx.impersonated_by});
      const exitBtn = document.createElement('button');
      exitBtn.textContent = T('btn_exit_tenant_panel');
      exitBtn.style.cssText = 'margin-left:1rem;padding:.25rem .75rem;font-size:.8rem;cursor:pointer;border:1px solid #172b4d;border-radius:4px;background:#fff;color:#172b4d';
      exitBtn.onclick = () => {
        const prev = localStorage.getItem('adminfoundry_prev_access');
        const prevR = localStorage.getItem('adminfoundry_prev_refresh');
        localStorage.removeItem('adminfoundry_prev_access');
        localStorage.removeItem('adminfoundry_prev_refresh');
        if (prev) {
          localStorage.setItem(TOKEN_KEY, prev);
          if (prevR) localStorage.setItem(REFRESH_KEY, prevR); else localStorage.removeItem(REFRESH_KEY);
        } else {
          Auth.clear();
        }
        // Redirect to root panel (strip subdomain)
        const parts = window.location.hostname.split('.');
        const rootHost = parts.length > 1 ? parts.slice(1).join('.') : parts[0];
        const port = window.location.port ? ':' + window.location.port : '';
        window.location.href = `${window.location.protocol}//${rootHost}${port}${UI_BASE}/dashboard`;
      };
      banner.appendChild(msg);
      banner.appendChild(exitBtn);
      document.body.prepend(banner);
    }

    if (ctx.tenant) {
      const tenantEl = document.getElementById('tenant-ctx');
      if (tenantEl) tenantEl.textContent = T('label_tenant_ctx', {slug: ctx.tenant.slug});
      if (ctx.tenant.locale) {
        window.ADMIN_TENANT_LOCALE = ctx.tenant.locale;
        applyI18n();
      }
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
  const selectedCountEl = document.getElementById('selected-count');
  const actionSelect = document.getElementById('action-select');
  const actionClearBtn = document.getElementById('action-clear');

  // Resolve selection count label; export actions don't need selection
  function _isExportAction(val) { return val && val.startsWith('__export_'); }

  function updateSelectionUI() {
    if (!selectedCountEl || !actionClearBtn) return;
    if (selected.size > 0) {
      selectedCountEl.style.display = '';
      selectedCountEl.textContent = T('label_n_selected', {count: selected.size});
      actionClearBtn.style.display = '';
    } else {
      selectedCountEl.style.display = 'none';
      actionClearBtn.style.display = 'none';
    }
  }

  // Trigger download from export endpoint
  function _doExport(fmt) {
    let url = `${API_BASE}/admin/${model}/export?format=${fmt}`;
    if (q) url += `&q=${encodeURIComponent(q)}`;
    if (orderBy) url += `&order_by=${encodeURIComponent(orderBy)}`;
    // Use tenant timezone (org reference) — falls back to UTC on server if unset
    const exportTz = window.ADMIN_TENANT_LOCALE?.timezone;
    if (exportTz) url += `&tz=${encodeURIComponent(exportTz)}`;
    const params = new URLSearchParams(window.location.search);
    params.forEach((v, k) => {
      if (!['q', 'order_by', 'page', 'page_size', 'format', 'tz'].includes(k))
        url += `&${k}=${encodeURIComponent(v)}`;
    });
    const token = Auth.getToken();
    fetch(url, { headers: token ? { Authorization: `Bearer ${token}` } : {} })
      .then(r => {
        if (!r.ok) throw new Error(`Export failed: ${r.status}`);
        const cd = r.headers.get('content-disposition') || '';
        const fname = cd.match(/filename="([^"]+)"/)?.[1] || `export.${fmt}`;
        return r.blob().then(blob => ({ blob, fname }));
      })
      .then(({ blob, fname }) => {
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = fname;
        a.click();
        URL.revokeObjectURL(a.href);
      })
      .catch(err => showError(err.message));
  }

  // Promise-based confirm modal (replaces window.confirm)
  function showConfirmModal(title, body, isDanger) {
    return new Promise(resolve => {
      const modal = document.getElementById('confirm-modal');
      if (!modal) { resolve(window.confirm(`${title}\n${body}`)); return; }
      document.getElementById('confirm-title').textContent = title;
      document.getElementById('confirm-body').textContent = body;
      const okBtn = document.getElementById('confirm-ok');
      const cancelBtn = document.getElementById('confirm-cancel');
      okBtn.className = `btn ${isDanger ? 'btn-danger' : 'btn-primary'}`;
      modal.style.display = 'flex';

      function cleanup(result) {
        modal.style.display = 'none';
        okBtn.removeEventListener('click', onOk);
        cancelBtn.removeEventListener('click', onCancel);
        resolve(result);
      }
      function onOk()     { cleanup(true);  }
      function onCancel() { cleanup(false); }
      okBtn.addEventListener('click', onOk);
      cancelBtn.addEventListener('click', onCancel);
    });
  }

  function initActionBar() {
    if (!actionBar || !actionSelect) return;

    const bulkActions = (meta?.actions || []).filter(a => a.bulk);
    const exportOptions = [
      { value: '__export_csv__',  label: T('option_export_csv')  },
      { value: '__export_json__', label: T('option_export_json') },
      { value: '__export_xlsx__', label: T('option_export_xlsx') },
    ];

    // Nothing to show — hide bar and return
    if (!bulkActions.length && !exportOptions.length) return;

    actionBar.style.display = 'flex';

    let html = bulkActions.map(a =>
      `<option value="${esc(a.name)}" data-danger="${a.danger}" data-confirm="${a.confirm}">${esc(a.label)}</option>`
    ).join('');

    if (bulkActions.length) html += `<option disabled>──────────</option>`;
    html += exportOptions.map(o => `<option value="${o.value}">${esc(o.label)}</option>`).join('');
    actionSelect.innerHTML = html;

    document.getElementById('action-run')?.addEventListener('click', async () => {
      const opt = actionSelect.selectedOptions[0];
      if (!opt) return;
      const actionName = opt.value;

      // Export actions — no selection required
      if (_isExportAction(actionName)) {
        const fmt = actionName.replace('__export_', '').replace('__', '');
        _doExport(fmt);
        return;
      }

      if (!selected.size) { showError(T('error_no_selection')); return; }

      const needsConfirm = opt.dataset.confirm === 'true';
      const isDanger = opt.dataset.danger === 'true';

      if (needsConfirm) {
        const label = opt.textContent.trim();
        const ok = await showConfirmModal(
          label,
          T('confirm_bulk_action', {label, count: selected.size}),
          isDanger,
        );
        if (!ok) return;
      }

      try {
        const result = await API.post(`/admin/${model}/bulk-action`, {
          action: actionName,
          object_ids: [...selected],
        });
        showSuccess(result.summary || T('label_action_done', {count: result.affected ?? selected.size}));
        selected.clear();
        updateSelectionUI();
        await load();
      } catch (err) {
        showError(fmtAPIError(err));
      }
    });

    actionClearBtn?.addEventListener('click', () => {
      selected.clear();
      updateSelectionUI();
      document.querySelectorAll('.row-check').forEach(cb => cb.checked = false);
      const selAll = document.getElementById('select-all');
      if (selAll) selAll.checked = false;
    });
  }

  async function load() {
    const tableEl = document.getElementById('table-body');
    const theadEl = document.getElementById('table-head');
    const paginEl = document.getElementById('pagination');
    tableEl.innerHTML = `<tr><td colspan="99" class="loading">${T('status_loading')}</td></tr>`;

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
        ? `<th style="width:36px"><input type="checkbox" id="select-all" aria-label="${T('aria_select_all')}"></th>`
        : '';
      theadEl.innerHTML = '<tr>' + checkboxTh + cols.map(c => {
        const fieldMeta = meta?.fields?.find(f => f.name === c);
        const label = fieldMeta?.label || c;
        const arrow = c === orderBy ? ' ↑' : c === `-${orderBy}`.replace('-','') ? ' ↓' : '';
        return `<th><a href="#" data-sort="${c}" style="color:inherit;text-decoration:none">${esc(label)}${arrow}</a></th>`;
      }).join('') + `<th style="width:120px">${T('table_header_actions')}</th></tr>`;

      // Rows
      const editableCols = new Set(meta?.list_editable || []);
      if (!data.items.length) {
        tableEl.innerHTML = `<tr><td colspan="99" style="color:#586069">${T('error_no_records')}</td></tr>`;
      } else {
        tableEl.innerHTML = data.items.map(item => {
          const id = item.id || '';
          const isChecked = selected.has(id) ? ' checked' : '';
          const checkTd = hasBulkActions
            ? `<td><input type="checkbox" class="row-check" data-id="${id}" aria-label="${T('aria_select_row')}"${isChecked}></td>`
            : '';
          const cells = cols.map(c => {
            if (editableCols.has(c)) {
              const v = item[c] ?? '';
              return `<td><input class="list-inline-input" data-id="${esc(id)}" data-field="${esc(c)}" value="${esc(String(v))}" aria-label="${esc(c)}"></td>`;
            }
            return `<td>${fmtCell(item[c])}</td>`;
          }).join('');
          const actions = `<td style="white-space:nowrap;width:1%;padding-right:.75rem">
            <a class="btn btn-sm btn-icon" href="${UI_BASE}/${model}/${id}" title="${T('action_view')}" aria-label="${T('action_view')}"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg></a>
            <a class="btn btn-sm btn-icon" href="${UI_BASE}/${model}/${id}/edit" title="${T('action_edit')}" aria-label="${T('action_edit')}"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg></a>
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
          updateSelectionUI();
        });
        tableEl.addEventListener('change', e => {
          const cb = e.target.closest('.row-check');
          if (!cb) return;
          if (cb.checked) selected.add(cb.dataset.id);
          else selected.delete(cb.dataset.id);
          updateSelectionUI();
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

    // "Enter tenant panel" button — only shown on Tenant detail for superadmin in root panel
    if (model === 'tenants' && item.slug && !_adminCtx?.is_impersonating && !_adminCtx?.tenant) {
      const enterBtn = document.createElement('button');
      enterBtn.className = 'btn';
      enterBtn.style.cssText = 'margin-top:1rem;display:inline-block';
      enterBtn.textContent = T('btn_enter_tenant_panel');
      enterBtn.onclick = async () => {
        enterBtn.disabled = true;
        try {
          const resp = await API.post(`/tenants/${objectId}/impersonate`, {});
          // Save current tokens so the exit button can restore them
          const cur = localStorage.getItem(TOKEN_KEY);
          const curR = localStorage.getItem(REFRESH_KEY);
          if (cur) localStorage.setItem('adminfoundry_prev_access', cur);
          if (curR) localStorage.setItem('adminfoundry_prev_refresh', curR);
          localStorage.setItem(TOKEN_KEY, resp.access_token);
          localStorage.removeItem(REFRESH_KEY);
          const port = window.location.port ? ':' + window.location.port : '';
          window.location.href = `${window.location.protocol}//${item.slug}.${window.location.hostname}${port}${UI_BASE}/dashboard`;
        } catch (e) {
          enterBtn.disabled = false;
          showError(fmtAPIError(e));
        }
      };
      bodyEl.appendChild(enterBtn);
    }

    // 4B: permission matrix section
    if (meta?.permission_matrix) {
      const matrixCard = document.createElement('div');
      matrixCard.className = 'card';
      matrixCard.style.marginTop = '1.25rem';
      matrixCard.innerHTML = `<h3 style="font-size:.95rem;font-weight:600;margin-bottom:1rem">${T('section_crud_permissions')}</h3><div id="permission-matrix-body"></div>`;
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
        historyEl.innerHTML = `<p style="color:#586069;font-size:.82rem">${T('error_no_changes')}</p>`;
      } else {
        const actionColor = { created: 'badge-green', updated: 'badge-gray', deleted: 'badge-red' };
        const truncate = (s, max = 80) => {
          const str = s == null ? '—' : String(s);
          return str.length > max ? str.slice(0, max) + '…' : str;
        };
        const entries = history.items.map(e => {
          const when = fmtDate(e.created_at);
          const color = actionColor[e.action] || 'badge-gray';
          const actor = e.actor || T('label_unknown_actor');
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
          ? `<a href="${UI_BASE}/audit_logs?q=${encodeURIComponent(objectId)}" style="font-size:.78rem;display:block;margin-top:.6rem;color:var(--link,#0052cc)">${T('link_view_all_changes', {count: remaining})}</a>`
          : '';
        historyEl.innerHTML = entries + moreLink;
      }
    } catch (err) {
      console.error('History load failed:', err);
      historyEl.innerHTML = `<p style="color:#586069;font-size:.82rem">${T('error_history_unavailable')}: ${esc(String(err))}</p>`;
    }
  }

  setBreadcrumb([
    { label: T('nav_home'), href: UI_BASE + '/dashboard' },
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
    document.getElementById('page-title').textContent = T('page_title_new', {label: meta.label || model});
    const backLink = document.getElementById('back-link');
    if (backLink) backLink.href = `${UI_BASE}/${model}`;

    const writableFields = (meta.fields || []).filter(
      f => !f.readonly && !['id', 'created_at', 'updated_at'].includes(f.name)
    );

    if (!writableFields.length) {
      formEl.innerHTML = `<div class="fallback-notice">${T('error_no_editable_fields')}</div>`;
      return;
    }

    // Rebuild form content — always include the submit button
    formEl.innerHTML =
      writableFields.map(f => buildFieldInput(f)).join('') +
      `<div style="margin-top:1rem"><button type="submit" class="btn btn-primary">${T('action_create')}</button></div>`;

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
    { label: T('nav_home'), href: UI_BASE + '/dashboard' },
    { label: meta?.label_plural || model, href: `${UI_BASE}/${model}` },
    { label: T('nav_new'), href: '' },
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

    document.getElementById('page-title').textContent = T('page_title_edit', {label: meta.label || model});
    const backLink = document.getElementById('back-link');
    if (backLink) backLink.href = `${UI_BASE}/${model}/${objectId}`;

    const editableFields = (meta.fields || []).filter(
      f => !['id', 'created_at', 'updated_at'].includes(f.name) && !f.create_only
    );

    if (!editableFields.length) {
      formEl.innerHTML = `<div class="fallback-notice">${T('error_no_editable_fields')}</div>`;
      return;
    }

    formEl.innerHTML =
      editableFields.map(f => buildFieldInput(f, item[f.name])).join('') +
      `<div style="margin-top:1rem"><button type="submit" class="btn btn-primary">${T('action_save')}</button></div>`;

    populateRelationSelects(formEl);

    formEl.addEventListener('submit', async e => {
      e.preventDefault();
      const writableFields = editableFields.filter(f => !f.readonly);
      const body = collectForm(formEl, writableFields);
      try {
        const updated = await API.patch(`/admin/${model}/${objectId}`, body);
        showSuccess(T('status_saved'));
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
      matrixCard.innerHTML = `<h3 style="font-size:.95rem;font-weight:600;margin-bottom:1rem">${T('section_crud_permissions')}</h3><div id="permission-matrix-body"></div>`;
      formEl.parentElement.insertAdjacentElement('afterend', matrixCard);
      renderPermissionMatrix(objectId, document.getElementById('permission-matrix-body'));
    }
  } catch (e) {
    formEl.innerHTML = `<div class="alert alert-error" style="display:block">${esc(fmtAPIError(e))}</div>`;
  }

  setBreadcrumb([
    { label: T('nav_home'), href: UI_BASE + '/dashboard' },
    { label: meta?.label_plural || model, href: `${UI_BASE}/${model}` },
    { label: objectId, href: `${UI_BASE}/${model}/${objectId}` },
    { label: T('nav_edit'), href: '' },
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

  const hint = isReadonly ? `<p class="field-hint">${T('label_readonly')}</p>` : '';
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
        sel.innerHTML = `<option value="">${T('option_select')}</option>` +
          data.items.map(item =>
            `<option value="${esc(item.id)}"${item.id === currentVal ? ' selected' : ''}>${esc(item.label)}</option>`
          ).join('');
      } catch (_) {
        sel.innerHTML = currentVal
          ? `<option value="${esc(currentVal)}" selected>${esc(currentVal)}</option>`
          : `<option value="">${T('option_none_available')}</option>`;
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
        sel.innerHTML = `<option value="">${T('option_select_model')}</option>` +
          opts.map(o =>
            `<option value="${esc(o.id)}"${o.id === currentVal ? ' selected' : ''}>${esc(o.label)}</option>`
          ).join('');
      } catch (_) {
        sel.innerHTML = currentVal
          ? `<option value="${esc(currentVal)}" selected>${esc(currentVal)}</option>`
          : `<option value="">${T('option_none_available')}</option>`;
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
  if (totalPages <= 1) { el.innerHTML = `<span>${T(totalItems === 1 ? 'pagination_item' : 'pagination_items', {count: totalItems})}</span>`; return; }

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

  el.innerHTML = prev + pages + next + `<span style="margin-left:.5rem;color:#586069">${T('pagination_total', {count: totalItems})}</span>`;
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

function applyStrftime(pattern, d) {
  const pad = (n, w = 2) => String(n).padStart(w, '0');
  const h12 = d.getHours() % 12 || 12;
  const offset = -d.getTimezoneOffset();
  const offSign = offset >= 0 ? '+' : '-';
  const offH = pad(Math.floor(Math.abs(offset) / 60));
  const offM = pad(Math.abs(offset) % 60);
  let tzAbbr = `UTC${offSign}${offH}:${offM}`;
  try {
    tzAbbr = new Intl.DateTimeFormat('en', { timeZoneName: 'short' })
      .formatToParts(d).find(p => p.type === 'timeZoneName')?.value || tzAbbr;
  } catch {}
  return pattern
    .replace('%Y', d.getFullYear())
    .replace('%y', pad(d.getFullYear() % 100))
    .replace('%m', pad(d.getMonth() + 1))
    .replace('%d', pad(d.getDate()))
    .replace('%H', pad(d.getHours()))
    .replace('%I', pad(h12))
    .replace('%M', pad(d.getMinutes()))
    .replace('%S', pad(d.getSeconds()))
    .replace('%p', d.getHours() < 12 ? 'AM' : 'PM')
    .replace('%z', `UTC${offSign}${offH}:${offM}`)
    .replace('%Z', tzAbbr);
}

function fmtDate(val) {
  if (!val) return '—';
  const d = new Date(val);
  if (isNaN(d)) return String(val);
  const pad = n => String(n).padStart(2, '0');
  const fmt = Prefs.getDateFormat();
  const showTz = Prefs.getShowTimezone();

  if (fmt === 'custom') {
    return applyStrftime(Prefs.getDatePattern() || '%Y-%m-%d %H:%M', d);
  }

  let tzSuffix = '';
  if (showTz) {
    try {
      const abbr = new Intl.DateTimeFormat('en', { timeZoneName: 'short' })
        .formatToParts(d).find(p => p.type === 'timeZoneName')?.value;
      if (abbr) tzSuffix = ` (${abbr})`;
    } catch {}
  }

  if (fmt === 'iso') {
    return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}${tzSuffix}`;
  }
  if (fmt === 'eu') {
    return `${pad(d.getDate())}.${pad(d.getMonth()+1)}.${d.getFullYear()}, ${pad(d.getHours())}:${pad(d.getMinutes())}${tzSuffix}`;
  }
  if (fmt === 'us') {
    return d.toLocaleString('en-US', { dateStyle: 'short', timeStyle: 'short' }) + tzSuffix;
  }
  // locale: delegate timezone display to Intl directly
  return showTz
    ? d.toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short', timeZoneName: 'short' })
    : d.toLocaleString();
}

function fmtCell(val) {
  if (val === null || val === undefined) return '<span style="color:#aaa">—</span>';
  if (typeof val === 'boolean') {
    return val
      ? '<span class="badge badge-green">Yes</span>'
      : '<span class="badge badge-red">No</span>';
  }
  if (typeof val === 'string' && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(val)) {
    return esc(fmtDate(val));
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
const PREFS_KEY = 'adminfoundry_prefs';
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
  _ld() { return window.ADMIN_LOCALE_DEFAULTS || {}; },
  _tl() { return window.ADMIN_TENANT_LOCALE || {}; },
  getLanguage() {
    return this.get().language || this._tl().language || this._ld().language
      || (navigator.language || '').split('-')[0] || 'en';
  },
  setLanguage(l) { this.set({language: l}); },
  getDateFormat() { return this.get().date_format || this._tl().date_format || this._ld().date_format || 'locale'; },
  setDateFormat(f) { this.set({date_format: f}); },
  getDatePattern() { return this.get().date_pattern || this._tl().date_pattern || this._ld().date_pattern || '%Y-%m-%d %H:%M'; },
  setDatePattern(p) { this.set({date_pattern: p}); },
  getShowTimezone() {
    const p = this.get();
    if ('show_timezone' in p) return p.show_timezone === true;
    const tl = this._tl();
    if ('show_timezone' in tl) return tl.show_timezone === true;
    return this._ld().show_timezone === true;
  },
  setShowTimezone(v) { this.set({show_timezone: v}); },
  getHomepage() { return this.get().homepage || 'dashboard'; },
  setHomepage(v) { this.set({homepage: v}); },
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
  setBreadcrumb([{ label: T('nav_settings') }]);
  await initNav(null);

  // --- Profile ---
  const nameEl = document.getElementById('pf-name');
  const emailEl = document.getElementById('pf-email');

  try {
    const me = await API.get('/admin/profile');
    if (nameEl) nameEl.value = me.full_name || '';
    if (emailEl) emailEl.value = me.email || '';
  } catch (e) {
    showError(T('error_profile_load_failed'));
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
      showSuccess(T('status_profile_saved'));
    } catch (err) {
      showError(fmtAPIError(err) || T('error_save_failed'));
    } finally {
      btn.disabled = false;
    }
  });

  // --- UI Preferences ---
  const densityEl = document.getElementById('pref-density');
  const pageSizeEl = document.getElementById('pref-page-size');
  const themeEl = document.getElementById('pref-theme');
  const dateFormatEl = document.getElementById('pref-date-format');
  const datePatternEl = document.getElementById('pref-date-pattern');
  const datePatternRow = document.getElementById('pref-date-pattern-row');
  const showTzEl = document.getElementById('pref-show-timezone');
  const langEl = document.getElementById('pref-language');
  const homepageEl = document.getElementById('pref-homepage');

  // Populate homepage dropdown with nav items
  if (homepageEl) {
    try {
      const nav = await API.get('/admin/navigation');
      const opts = nav.items.map(item =>
        `<option value="${esc(item.model)}">${esc(item.label_plural)}</option>`
      ).join('');
      homepageEl.innerHTML =
        `<option value="dashboard">${T('option_homepage_dashboard')}</option>` + opts;
    } catch {
      homepageEl.innerHTML = `<option value="dashboard">${T('option_homepage_dashboard')}</option>`;
    }
    homepageEl.value = Prefs.getHomepage();
  }

  densityEl.value = Prefs.getDensity();
  pageSizeEl.value = String(Prefs.getPageSize());
  themeEl.value = Prefs.getTheme();
  if (dateFormatEl) {
    dateFormatEl.value = Prefs.getDateFormat();
    if (datePatternEl) datePatternEl.value = Prefs.getDatePattern();
    if (datePatternRow) datePatternRow.style.display = dateFormatEl.value === 'custom' ? '' : 'none';
    dateFormatEl.addEventListener('change', () => {
      if (datePatternRow) datePatternRow.style.display = dateFormatEl.value === 'custom' ? '' : 'none';
    });
  }
  if (showTzEl) showTzEl.checked = Prefs.getShowTimezone();
  if (langEl) langEl.value = Prefs.getLanguage();

  document.getElementById('prefs-save-btn').addEventListener('click', () => {
    Prefs.setDensity(densityEl.value);
    Prefs.setPageSize(parseInt(pageSizeEl.value, 10));
    Prefs.setTheme(themeEl.value);
    if (dateFormatEl) Prefs.setDateFormat(dateFormatEl.value);
    if (datePatternEl) Prefs.setDatePattern(datePatternEl.value.trim() || '%Y-%m-%d %H:%M');
    if (showTzEl) Prefs.setShowTimezone(showTzEl.checked);
    if (langEl) Prefs.setLanguage(langEl.value);
    if (homepageEl) Prefs.setHomepage(homepageEl.value);
    const theme = themeEl.value === 'auto'
      ? (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')
      : themeEl.value;
    DarkMode.apply(theme);
    applyI18n();
    showToast(T('status_prefs_saved'), 'success');
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
    document.getElementById('page-title').textContent = T('page_title_breakglass', {label: meta.label || model});

    // Only show non-protected, non-readonly fields as editable targets
    const editableFields = (meta.fields || []).filter(
      f => !f.readonly && !['id', 'created_at', 'updated_at'].includes(f.name)
    );

    if (!editableFields.length) {
      fieldsEl.innerHTML = `<div class="fallback-notice">${T('error_no_breakglass_fields')}</div>`;
    } else {
      fieldsEl.innerHTML = `<p style="font-size:.85rem;color:#586069;margin-bottom:.75rem">${T('text_select_fields_to_change')}</p>` +
        editableFields.map(f => buildFieldInput(f)).join('');
    }

    formEl.addEventListener('submit', async e => {
      e.preventDefault();
      const reasonEl = document.getElementById('bg-reason');
      const reasonErrEl = document.getElementById('reason-error');
      const reason = reasonEl ? reasonEl.value.trim() : '';

      if (reason.length < 10) {
        if (reasonErrEl) { reasonErrEl.textContent = T('error_reason_too_short'); reasonErrEl.style.display = 'block'; }
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
        showError(T('error_no_fields_changed'));
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

  const closeBtn = `<button onclick="document.getElementById('inline-create-modal').remove()" style="position:absolute;top:.75rem;right:.75rem;background:none;border:none;font-size:1.2rem;cursor:pointer;color:var(--text-muted,#586069)" aria-label="${T('aria_close')}">✕</button>`;

  panel.innerHTML = closeBtn + `<h3 style="font-size:1rem;margin-bottom:1rem">${T('modal_new_title', {table: targetTable})}</h3><div id="inline-form-wrap"><p class="loading">${T('status_loading')}</p></div>`;
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
        `<button type="submit" class="btn btn-primary">${T('action_create')}</button>` +
        `<button type="button" class="btn btn-secondary" onclick="document.getElementById('inline-create-modal').remove()">${T('action_cancel')}</button>` +
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
  containerEl.innerHTML = `<p class="loading">${T('status_loading_permissions')}</p>`;
  try {
    const matrix = await API.get(`/admin/permission-matrix/${objectId}`);

    const ops = ['can_list', 'can_create', 'can_update', 'can_delete'];
    const opLabels = {
      can_list: T('table_header_list'), can_create: T('table_header_create'),
      can_update: T('table_header_update'), can_delete: T('table_header_delete'),
    };

    const thead = `<thead><tr><th>${T('table_header_model')}</th>` + ops.map(o => `<th style="text-align:center">${opLabels[o]}</th>`).join('') + '</tr></thead>';
    const tbody = matrix.map(row =>
      `<tr><td style="font-weight:500">${esc(row.label || row.model_name)}</td>` +
      ops.map(op =>
        `<td style="text-align:center"><input type="checkbox" data-model="${esc(row.model_name)}" data-op="${esc(op)}"${row[op] ? ' checked' : ''}></td>`
      ).join('') +
      '</tr>'
    ).join('');

    containerEl.innerHTML =
      `<table style="width:100%">${thead}<tbody>${tbody}</tbody></table>` +
      `<button id="matrix-save" class="btn btn-primary" style="margin-top:1rem">${T('action_save_permissions')}</button>`;

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
        showSuccess(T('status_permissions_saved'));
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
  PREF_KEY: 'adminfoundry_theme',
  apply(theme) {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem(this.PREF_KEY, theme);
    const btn = document.getElementById('dark-mode-btn');
    if (btn) btn.textContent = theme === 'dark' ? T('btn_light_mode') : T('btn_dark_mode');
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
document.addEventListener('DOMContentLoaded', applyI18n);

// ---------------------------------------------------------------------------
// Dashboard
// ---------------------------------------------------------------------------
async function initDashboard() {
  if (!Auth.isLoggedIn()) { Auth.redirectToLogin(); return; }

  const hp = Prefs.getHomepage();
  if (hp && hp !== 'dashboard') {
    window.location.replace(`${UI_BASE}/${hp}`);
    return;
  }

  await initNav(null);

  const el = document.getElementById('dashboard-metrics');
  if (!el) return;

  function statCard(label, value, sub) {
    return `<div class="dash-stat">
      <div class="dash-stat-value">${esc(String(value))}</div>
      <div class="dash-stat-label">${esc(label)}</div>
      ${sub ? `<div class="dash-stat-sub">${esc(sub)}</div>` : ''}
    </div>`;
  }

  function renderWidget(w) {
    let inner = '';
    if (w.type === 'counts') {
      const rows = w.data.rows || [];
      if (!rows.length) {
        inner = `<p style="color:var(--text-muted,#586069);font-size:.875rem">No models registered.</p>`;
      } else {
        inner = `<table style="width:100%;font-size:.875rem">
          <thead><tr>
            <th style="text-align:left;padding:.35rem .5rem;border-bottom:2px solid var(--border,#e1e4e8);color:var(--text-muted,#586069)">Model</th>
            <th style="text-align:right;padding:.35rem .5rem;border-bottom:2px solid var(--border,#e1e4e8);color:var(--text-muted,#586069)">Records</th>
          </tr></thead>
          <tbody>${rows.map(r =>
            `<tr>
              <td style="padding:.35rem .5rem;border-bottom:1px solid var(--border,#f0f0f0)">
                <a href="${UI_BASE}/${esc(r.model)}" style="color:var(--link,#0052cc);text-decoration:none">${esc(r.label)}</a>
              </td>
              <td style="padding:.35rem .5rem;border-bottom:1px solid var(--border,#f0f0f0);text-align:right;font-variant-numeric:tabular-nums">${r.count}</td>
            </tr>`
          ).join('')}</tbody>
        </table>`;
      }
    } else {
      // stats type
      const stats = w.data.stats || [];
      inner = `<div class="dash-stats">${stats.map(s => statCard(s.label, s.value, s.sub)).join('')}</div>`;
      const clientStats = w.data.client_stats || [];
      if (clientStats.length) {
        inner += `<div class="dash-section"><h3 class="dash-section-title">Client types</h3>
          <div class="dash-stats">${clientStats.map(s => statCard(s.label, s.value)).join('')}</div>
        </div>`;
      }
    }
    return `<div class="dash-widget card">
      <h2 class="dash-widget-title">${esc(w.title)}</h2>
      ${inner}
    </div>`;
  }

  try {
    const { widgets } = await API.get('/admin/dashboard');
    el.innerHTML = widgets.length
      ? widgets.map(renderWidget).join('')
      : `<p style="color:var(--text-muted,#586069);font-size:.875rem">No dashboard widgets configured.</p>`;
  } catch {
    el.innerHTML = `<p style="color:var(--text-muted,#586069);font-size:.875rem">Dashboard unavailable.</p>`;
  }
}

// Expose globally
window.AdminUI = { Auth, API, Prefs, DarkMode, T, applyI18n, initNav, initList, initDetail, initCreate, initUpdate, initConfirmDelete, initBreakGlass, initSettings, initDashboard, openInlineCreate, showToast, showError, showSuccess };
