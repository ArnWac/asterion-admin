// Permission-matrix view (Roadmap 5.2b).
//
// Renders the roles × permissions grid backed by GET
// /_permission_matrix; the user toggles checkboxes and clicks Save,
// which PUTs only the rows whose contents diverged from the
// initially-loaded state. Keeps the request small (the diff
// detection is local) and the optimistic-UI surface tiny: on save
// we reload the matrix from the server so the displayed state and
// the DB state are guaranteed to agree.

import { admin, APIError } from "../api.js";
import { el, mount, setBreadcrumb, showToast } from "../dom.js";

const cfg = window.ASTERION || {};

// ---------------------------------------------------------------------------
// Diff (exported for unit testing)
// ---------------------------------------------------------------------------

/**
 * Compute the per-role assignment diff between two matrix snapshots.
 *
 * Returns ``{role_id: [keys]}`` for every role whose current set
 * differs from the baseline. Empty result means "no changes" — caller
 * skips the network call entirely.
 *
 * Both inputs are plain ``{role_id: [keys]}`` objects. Order doesn't
 * matter — sets are compared.
 */
export function diffAssignments(baseline, current) {
  const out = {};
  const roleIds = new Set([
    ...Object.keys(baseline || {}),
    ...Object.keys(current || {}),
  ]);
  for (const rid of roleIds) {
    const a = new Set((baseline && baseline[rid]) || []);
    const b = new Set((current && current[rid]) || []);
    if (a.size !== b.size || [...a].some((k) => !b.has(k))) {
      out[rid] = [...b].sort();
    }
  }
  return out;
}

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

function _groupByCategory(permissions) {
  // PermissionCatalog rows arrive sorted (category, key) from the
  // server, so we can build groups in one pass.
  const groups = [];
  let last = null;
  for (const p of permissions || []) {
    const cat = p.category || "uncategorized";
    if (!last || last.category !== cat) {
      last = { category: cat, items: [] };
      groups.push(last);
    }
    last.items.push(p);
  }
  return groups;
}

function _checkboxId(roleId, permKey) {
  return `pm-cell-${roleId}-${permKey.replace(/\W+/g, "_")}`;
}

// ---------------------------------------------------------------------------
// View
// ---------------------------------------------------------------------------

export async function mountPermissionMatrix(root) {
  setBreadcrumb([
    { label: "Home", href: `${cfg.uiPath}/dashboard` },
    { label: "Permissions" },
  ]);

  let matrix;
  try {
    matrix = await admin.permissionMatrix();
  } catch (err) {
    const message = err instanceof APIError ? err.message : String(err);
    mount(root, el("div", { class: "card" }, el("p", { class: "form-error" }, message)));
    return;
  }

  // Track current state as nested mutable Sets per role. Baseline
  // stays the array snapshot we got from the server.
  const baseline = matrix.assignments || {};
  const current = {};
  for (const rid of Object.keys(baseline)) {
    current[rid] = new Set(baseline[rid]);
  }

  const groups = _groupByCategory(matrix.permissions);
  const roles = matrix.roles || [];

  // --- header row: role names + system marker
  const headerCells = [el("th", { scope: "col", class: "pm-corner" }, "Permission")];
  for (const role of roles) {
    headerCells.push(
      el("th", { scope: "col" }, [
        el("span", { class: "pm-role-name" }, role.name),
        role.is_system
          ? el("span", { class: "pm-system-flag", title: "System role — read-only" }, " 🔒")
          : null,
      ])
    );
  }
  const thead = el("thead", {}, el("tr", {}, headerCells));

  // --- body: one section per category, one row per permission
  const tbody = el("tbody", {});
  for (const group of groups) {
    tbody.appendChild(
      el(
        "tr",
        { class: "pm-category" },
        el("th", { colSpan: 1 + roles.length, scope: "rowgroup" }, group.category)
      )
    );
    for (const perm of group.items) {
      const cells = [el("th", { scope: "row" }, perm.key)];
      for (const role of roles) {
        const id = _checkboxId(role.id, perm.key);
        const checkbox = el("input", {
          type: "checkbox",
          id,
          "aria-label": `${role.name} → ${perm.key}`,
          checked: current[role.id]?.has(perm.key) ? "" : null,
          disabled: role.is_system ? "" : null,
        });
        if (!role.is_system) {
          checkbox.addEventListener("change", () => {
            const set = current[role.id] ?? new Set();
            if (checkbox.checked) set.add(perm.key);
            else set.delete(perm.key);
            current[role.id] = set;
            saveButton.disabled = false;
          });
        }
        cells.push(el("td", { class: "pm-cell" }, checkbox));
      }
      tbody.appendChild(el("tr", {}, cells));
    }
  }

  // --- save button — disabled until the user touches something
  const saveButton = el(
    "button",
    { type: "button", class: "btn", disabled: "" },
    "Save changes"
  );
  saveButton.addEventListener("click", async () => {
    saveButton.disabled = true;
    const currentArrays = {};
    for (const rid of Object.keys(current)) {
      currentArrays[rid] = [...current[rid]];
    }
    const diff = diffAssignments(baseline, currentArrays);
    if (Object.keys(diff).length === 0) {
      showToast?.("No changes to save.", "info");
      return;
    }
    try {
      const updated = await admin.permissionMatrixSave(diff);
      // Re-render from the server's authoritative response — keeps
      // the form consistent with what actually landed in the DB.
      mountPermissionMatrix(root);
      showToast?.("Permissions saved.", "success");
      void updated; // server response also pinned by tests if needed
    } catch (err) {
      const message = err instanceof APIError ? err.message : String(err);
      showToast?.(`Save failed: ${message}`, "error");
      saveButton.disabled = false;
    }
  });

  mount(
    root,
    el("div", { class: "page-header" }, [
      el("h1", {}, "Permission matrix"),
      el("div", { class: "page-actions" }, saveButton),
    ]),
    el(
      "div",
      { class: "card pm-scroll" },
      roles.length === 0
        ? el("p", { class: "muted" }, "No tenant roles configured yet.")
        : el("table", { class: "pm-table" }, thead, tbody)
    )
  );
}
