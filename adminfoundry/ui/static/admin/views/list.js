// List view: paginated, searchable table for one resource.
// Supports bulk admin actions declared in admin_actions.

import { APIError, admin } from "../api.js";
import { getFullContract, getResourceContract } from "../contract.js";
import { clear, el, mount, setBreadcrumb, showToast } from "../dom.js";
import { formatValue } from "../format.js";
import { openImportModal } from "./import_modal.js";

const cfg = window.ADMINFOUNDRY || {};
const PAGE_SIZE = 25;

export async function mountList(root, resource) {
  const contract = await getResourceContract(resource);
  setBreadcrumb([
    { label: "Home", href: `${cfg.uiPath}/dashboard` },
    { label: contract.label_plural },
  ]);
  const pkField = contract.fields.find((f) => f.primary_key) || { name: "id" };
  const columns = (contract.list_display && contract.list_display.length
    ? contract.list_display
    : contract.fields.map((f) => f.name).slice(0, 5));

  // Per-user list preferences (Roadmap 5.5): density + hidden columns,
  // persisted in localStorage keyed by resource.
  const prefs = listPrefs(resource);
  const hiddenCols = new Set(prefs.getHiddenColumns().filter((c) => columns.includes(c)));
  const visibleColumns = () => columns.filter((c) => !hiddenCols.has(c));
  let density = prefs.getDensity();

  const state = {
    offset: 0,
    search: "",
    selectedIds: new Set(),
    items: [],
    // Active sort, e.g. "title" / "-created_at" / null (server default).
    ordering: prefs.getOrdering(),
  };
  // A column is sortable when it maps to a real (non-calculated) field.
  const fieldsByNameAll = Object.fromEntries(contract.fields.map((f) => [f.name, f]));
  const isSortable = (colName) =>
    !!fieldsByNameAll[colName] && !fieldsByNameAll[colName].calculated;

  const searchInput = el("input", {
    type: "search",
    placeholder: contract.search_fields.length
      ? `Search ${contract.label_plural.toLowerCase()}…`
      : "Search disabled (no search_fields)",
    "aria-label": "Search",
    disabled: contract.search_fields.length === 0,
  });

  const actionSelect = el(
    "select",
    { "aria-label": "Bulk action" },
    [el("option", { value: "" }, "— Bulk action —")].concat(
      (contract.admin_actions || []).map((a) =>
        el("option", { value: a.name }, a.label || a.name)
      )
    )
  );
  const actionRun = el("button", { class: "btn btn-sm", disabled: true }, "Run");
  const selectedCount = el("span", { class: "field-hint" }, "0 selected");

  const tableHead = el("thead");
  const tableBody = el("tbody");
  const paginationBar = el("nav", { class: "pagination", "aria-label": "Pagination" });

  const newBtn = el(
    "a",
    { class: "btn btn-primary", href: `${cfg.uiPath}/${resource}/new` },
    "+ New"
  );

  // Import/Export buttons — rendered only when the import_export
  // extension is actually mounted server-side. The full contract's
  // `extensions.import_export` fragment is the source of truth (it also
  // lists the formats usable in this install), so we never show a button
  // that would 404 or a format the server would 501 on.
  const fullContract = await getFullContract();
  const ieCap = (fullContract.extensions && fullContract.extensions.import_export) || null;
  const exportFormats = (ieCap && ieCap.export_formats) || [];

  const importBtn = ieCap
    ? el("button", { type: "button", class: "btn" }, "Import")
    : null;
  const exportCsvBtn = exportFormats.includes("csv")
    ? el("button", { type: "button", class: "btn" }, "Export CSV")
    : null;
  const exportXlsxBtn = exportFormats.includes("xlsx")
    ? el("button", { type: "button", class: "btn" }, "Export XLSX")
    : null;

  if (importBtn) {
    importBtn.addEventListener("click", () =>
      openImportModal(resource, contract, () => load(), ieCap)
    );
  }
  if (exportCsvBtn) exportCsvBtn.addEventListener("click", () => doExport("csv"));
  if (exportXlsxBtn) exportXlsxBtn.addEventListener("click", () => doExport("xlsx"));

  async function doExport(format) {
    const ids = Array.from(state.selectedIds);
    try {
      await admin.exportDownload(resource, {
        format,
        search: state.search,
        ids,  // empty array → server falls back to full (search-filtered) export
      });
    } catch (err) {
      const message = err instanceof APIError ? err.message : String(err);
      showToast(`Export failed: ${message}`, { type: "error" });
    }
  }

  function refreshExportLabels() {
    const n = state.selectedIds.size;
    const suffix = n > 0 ? ` (${n} selected)` : "";
    if (exportCsvBtn) exportCsvBtn.textContent = `Export CSV${suffix}`;
    if (exportXlsxBtn) exportXlsxBtn.textContent = `Export XLSX${suffix}`;
  }

  // --- list display controls (Roadmap 5.5): density + column visibility ---
  const table = el("table", { class: density === "compact" ? "compact-table" : "" }, [
    tableHead,
    tableBody,
  ]);

  const densityBtn = el("button", { type: "button", class: "btn btn-sm" });
  function syncDensityBtn() {
    densityBtn.textContent = density === "compact" ? "Comfortable" : "Compact";
    densityBtn.setAttribute("aria-pressed", density === "compact" ? "true" : "false");
  }
  syncDensityBtn();
  densityBtn.addEventListener("click", () => {
    density = density === "compact" ? "comfortable" : "compact";
    table.classList.toggle("compact-table", density === "compact");
    prefs.setDensity(density);
    syncDensityBtn();
  });

  const columnsMenu = el("details", { class: "columns-menu" }, [
    el("summary", { class: "btn btn-sm" }, "Columns"),
    el(
      "div",
      { class: "columns-menu-panel" },
      columns.map((colName) =>
        el("label", { class: "columns-menu-item" }, [
          el("input", {
            type: "checkbox",
            checked: !hiddenCols.has(colName),
            onChange: (e) => {
              if (e.target.checked) hiddenCols.delete(colName);
              else hiddenCols.add(colName);
              prefs.setHiddenColumns(Array.from(hiddenCols));
              renderHead();
              renderRows(state.items);
            },
          }),
          el("span", {}, prettify(colName)),
        ])
      )
    ),
  ]);

  const displayControls = el("div", { class: "list-controls" }, [densityBtn, columnsMenu]);

  const layout = el("div", {}, [
    el("div", { class: "page-header" }, [
      el("h1", {}, contract.label_plural),
      el("div", { class: "page-actions" }, [
        importBtn, exportCsvBtn, exportXlsxBtn, newBtn,
      ].filter(Boolean)),
    ]),
    el("div", { class: "card" }, [
      el("div", { class: "toolbar" }, [
        searchInput,
        (contract.admin_actions || []).length
          ? el("div", { class: "toolbar", style: "padding:0;border:none" }, [
              actionSelect,
              actionRun,
              selectedCount,
            ])
          : null,
        displayControls,
      ]),
      el("div", { style: "overflow-x:auto" }, table),
      paginationBar,
    ]),
  ]);

  mount(root, layout);

  // --- rendering ---

  function renderHead() {
    const cols = visibleColumns();
    const ths = [el("th", { class: "checkbox-cell" }, [
      el("input", { type: "checkbox", "aria-label": "Select all on this page", onChange: toggleAll }),
    ])];
    for (const colName of cols) {
      if (isSortable(colName)) {
        const asc = state.ordering === colName;
        const desc = state.ordering === `-${colName}`;
        const indicator = asc ? " ▲" : desc ? " ▼" : "";
        ths.push(
          el("th", {}, [
            el(
              "button",
              {
                type: "button",
                class: "th-sort" + (asc || desc ? " active" : ""),
                "aria-label": `Sort by ${prettify(colName)}`,
                onClick: () => toggleSort(colName),
              },
              prettify(colName) + indicator
            ),
          ])
        );
      } else {
        ths.push(el("th", {}, prettify(colName)));
      }
    }
    ths.push(el("th", { class: "actions" }, ""));
    clear(tableHead);
    tableHead.appendChild(el("tr", {}, ths));
  }

  function toggleSort(colName) {
    // Cycle per column: ascending → descending → off (server default).
    if (state.ordering === colName) state.ordering = `-${colName}`;
    else if (state.ordering === `-${colName}`) state.ordering = null;
    else state.ordering = colName;
    prefs.setOrdering(state.ordering);
    state.offset = 0;
    load();
  }

  function renderRows(items) {
    state.items = items;
    const cols = visibleColumns();
    clear(tableBody);
    if (items.length === 0) {
      tableBody.appendChild(
        el("tr", {}, el("td", { colspan: cols.length + 2, class: "placeholder" }, "No records."))
      );
      return;
    }
    const fieldsByName = Object.fromEntries(contract.fields.map((f) => [f.name, f]));
    const badges = contract.list_badges || {};
    for (const item of items) {
      const id = String(item[pkField.name]);
      const checkbox = el("input", {
        type: "checkbox",
        "aria-label": `Select ${id}`,
        checked: state.selectedIds.has(id),
        onChange: (e) => toggleOne(id, e.target.checked),
      });
      const cells = [el("td", { class: "checkbox-cell" }, [checkbox])];
      for (const colName of cols) {
        // Badge styling (Roadmap 5.5): a configured value renders as a
        // colored chip instead of plain formatted text.
        const badgeStyle =
          item[colName] != null && badges[colName]
            ? badges[colName][String(item[colName])]
            : undefined;
        if (badgeStyle) {
          cells.push(
            el("td", {}, [
              el("span", { class: `badge badge-${badgeStyle}` }, String(item[colName])),
            ])
          );
          continue;
        }
        const formatted = formatValue(item[colName], fieldsByName[colName]);
        const td = el("td", { class: formatted.muted ? "muted" : "" }, formatted.text);
        if (formatted.mono) td.style.fontFamily = "ui-monospace, SFMono-Regular, monospace";
        cells.push(td);
      }
      cells.push(
        el("td", { class: "actions" }, [
          el(
            "a",
            { class: "btn btn-sm", href: `${cfg.uiPath}/${resource}/${encodeURIComponent(id)}` },
            "View"
          ),
        ])
      );
      tableBody.appendChild(el("tr", {}, cells));
    }
  }

  function renderPagination(total) {
    clear(paginationBar);
    const start = total === 0 ? 0 : state.offset + 1;
    const end = Math.min(state.offset + PAGE_SIZE, total);
    paginationBar.appendChild(el("span", {}, `${start}–${end} of ${total}`));
    paginationBar.appendChild(el("span", { class: "spacer" }));
    paginationBar.appendChild(
      el(
        "button",
        {
          class: "btn btn-sm",
          disabled: state.offset === 0,
          onClick: () => go(Math.max(0, state.offset - PAGE_SIZE)),
        },
        "‹ Prev"
      )
    );
    paginationBar.appendChild(
      el(
        "button",
        {
          class: "btn btn-sm",
          disabled: state.offset + PAGE_SIZE >= total,
          onClick: () => go(state.offset + PAGE_SIZE),
        },
        "Next ›"
      )
    );
  }

  function refreshSelectionUI() {
    actionRun.disabled = state.selectedIds.size === 0 || !actionSelect.value;
    selectedCount.textContent = `${state.selectedIds.size} selected`;
    refreshExportLabels();
  }

  // --- handlers ---

  function toggleAll(e) {
    const checked = e.target.checked;
    const rowCheckboxes = tableBody.querySelectorAll("input[type=checkbox]");
    rowCheckboxes.forEach((cb) => {
      cb.checked = checked;
    });
    if (checked) {
      tableBody.querySelectorAll("tr").forEach((tr, idx) => {
        const cb = tr.querySelector("input[type=checkbox]");
        if (cb && cb.getAttribute("aria-label")) {
          const id = cb.getAttribute("aria-label").replace(/^Select /, "");
          state.selectedIds.add(id);
        }
      });
    } else {
      state.selectedIds.clear();
    }
    refreshSelectionUI();
  }

  function toggleOne(id, checked) {
    if (checked) state.selectedIds.add(id);
    else state.selectedIds.delete(id);
    refreshSelectionUI();
  }

  function go(offset) {
    state.offset = offset;
    load();
  }

  async function load() {
    try {
      const data = await admin.list(resource, {
        limit: PAGE_SIZE,
        offset: state.offset,
        search: state.search,
        ordering: state.ordering || "",
      });
      renderHead();
      renderRows(data.items || []);
      renderPagination(data.total || 0);
      state.selectedIds.clear();
      refreshSelectionUI();
    } catch (err) {
      const message = err instanceof APIError ? err.message : String(err);
      showToast(`Load failed: ${message}`, { type: "error" });
    }
  }

  let searchTimer;
  searchInput.addEventListener("input", (e) => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      state.search = e.target.value.trim();
      state.offset = 0;
      load();
    }, 250);
  });

  actionSelect.addEventListener("change", refreshSelectionUI);

  actionRun.addEventListener("click", async () => {
    const action = actionSelect.value;
    const ids = Array.from(state.selectedIds);
    if (!action || !ids.length) return;
    const ok = confirm(
      `Run "${action}" on ${ids.length} record(s)? This cannot be undone.`
    );
    if (!ok) return;
    actionRun.disabled = true;
    try {
      const res = await admin.runAction(resource, action, ids);
      showToast(res.summary || `${action} ok`);
      load();
    } catch (err) {
      const message = err instanceof APIError ? err.message : String(err);
      showToast(`Action failed: ${message}`, { type: "error" });
    } finally {
      refreshSelectionUI();
    }
  });

  load();
}

function prettify(name) {
  return name.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

// Per-user list preferences (Roadmap 5.5), persisted in localStorage and
// scoped by resource. All access is wrapped in try/catch so a disabled or
// full localStorage degrades to in-memory defaults rather than throwing.
function listPrefs(resource) {
  const key = (k) => `af:list:${resource}:${k}`;
  const read = (k, fallback) => {
    try {
      const v = localStorage.getItem(key(k));
      return v == null ? fallback : JSON.parse(v);
    } catch {
      return fallback;
    }
  };
  const write = (k, v) => {
    try {
      localStorage.setItem(key(k), JSON.stringify(v));
    } catch {
      /* ignore — preferences are best-effort */
    }
  };
  return {
    getDensity: () => (read("density", "comfortable") === "compact" ? "compact" : "comfortable"),
    setDensity: (d) => write("density", d),
    getHiddenColumns: () => {
      const v = read("hiddenCols", []);
      return Array.isArray(v) ? v : [];
    },
    setHiddenColumns: (arr) => write("hiddenCols", arr),
    getOrdering: () => {
      const v = read("ordering", null);
      return typeof v === "string" && v ? v : null;
    },
    setOrdering: (v) => write("ordering", v),
  };
}
