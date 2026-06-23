// Per-role permission picker — Django-style two-list assign/unassign.
//
// Backed by the existing /_permission_matrix API (GET all roles +
// permissions + assignments; PUT the assignments for this one role). We
// only render/edit the single role named by the URL's record id.

import { admin, APIError } from "../api.js";
import { el, mount, setBreadcrumb, showToast } from "../dom.js";

const cfg = window.ASTERION || {};

export async function mountRolePermissions(root, resource, roleId) {
  setBreadcrumb([
    { label: "Home", href: `${cfg.uiPath}/dashboard` },
    { label: "Tenant Roles", href: `${cfg.uiPath}/${resource}` },
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

  const role = (matrix.roles || []).find((r) => String(r.id) === String(roleId));
  if (!role) {
    mount(root, el("div", { class: "card" }, el("p", { class: "form-error" }, "Role not found.")));
    return;
  }

  const allKeys = (matrix.permissions || []).map((p) => p.key);
  const assigned = new Set((matrix.assignments && matrix.assignments[role.id]) || []);
  const readOnly = !!role.is_system;
  let dirty = false;

  const availBox = el("select", {
    multiple: "",
    size: "16",
    class: "dual-list",
    "aria-label": "Available permissions",
  });
  const assignedBox = el("select", {
    multiple: "",
    size: "16",
    class: "dual-list",
    "aria-label": "Assigned permissions",
  });
  const saveBtn = el("button", { type: "button", class: "btn btn-primary", disabled: "" }, "Save");

  function rebuild() {
    availBox.replaceChildren(
      ...allKeys.filter((k) => !assigned.has(k)).map((k) => el("option", { value: k }, k))
    );
    assignedBox.replaceChildren(
      ...allKeys.filter((k) => assigned.has(k)).map((k) => el("option", { value: k }, k))
    );
    saveBtn.disabled = readOnly || !dirty;
  }

  function move(fromBox, add) {
    if (readOnly) return;
    const keys = [...fromBox.selectedOptions].map((o) => o.value);
    if (keys.length === 0) return;
    for (const k of keys) (add ? assigned.add(k) : assigned.delete(k));
    dirty = true;
    rebuild();
  }

  const addBtn = el(
    "button",
    { type: "button", class: "btn", disabled: readOnly ? "" : null },
    "Add →"
  );
  const removeBtn = el(
    "button",
    { type: "button", class: "btn", disabled: readOnly ? "" : null },
    "← Remove"
  );
  addBtn.addEventListener("click", () => move(availBox, true));
  removeBtn.addEventListener("click", () => move(assignedBox, false));

  saveBtn.addEventListener("click", async () => {
    saveBtn.disabled = true;
    try {
      await admin.permissionMatrixSave({ [role.id]: [...assigned] });
      dirty = false;
      showToast("Permissions saved.", { type: "ok" });
      rebuild();
    } catch (err) {
      const message = err instanceof APIError ? err.message : String(err);
      showToast(`Save failed: ${message}`, { type: "error" });
      saveBtn.disabled = false;
    }
  });

  rebuild();

  mount(
    root,
    el("div", { class: "page-header" }, [
      el("h1", {}, `Permissions — ${role.name}`),
      el("div", { class: "page-actions" }, saveBtn),
    ]),
    readOnly
      ? el(
          "div",
          { class: "card" },
          el("p", { class: "muted" }, "System role — permissions are read-only.")
        )
      : null,
    el("div", { class: "card dual-list-wrap" }, [
      el("div", { class: "dual-col" }, [el("label", {}, "Available"), availBox]),
      el("div", { class: "dual-col dual-actions" }, [addBtn, removeBtn]),
      el("div", { class: "dual-col" }, [el("label", {}, "Assigned"), assignedBox]),
    ])
  );
}
