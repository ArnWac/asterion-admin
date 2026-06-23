// Detail view: read-only render of one record.

import { APIError, admin } from "../api.js";
import { getResourceContract } from "../contract.js";
import { looksLikeAuditDiff, renderDiffTable } from "../diff.js";
import { el, mount, setBreadcrumb } from "../dom.js";
import { formatValue } from "../format.js";
import { renderImpersonateButton } from "../impersonation.js";
import { openTenant } from "../tenant_access.js";

const cfg = window.ASTERION || {};

export async function mountDetail(root, resource, recordId) {
  const contract = await getResourceContract(resource);
  setBreadcrumb([
    { label: "Home", href: `${cfg.uiPath}/dashboard` },
    { label: contract.label_plural, href: `${cfg.uiPath}/${resource}` },
    { label: prettify(recordId) },
  ]);

  let record;
  try {
    record = await admin.read(resource, recordId);
  } catch (err) {
    const message = err instanceof APIError ? err.message : String(err);
    mount(root, errorScreen(message));
    return;
  }

  const grid = el("dl", { class: "detail-grid" });
  for (const field of contract.fields) {
    grid.appendChild(el("dt", {}, prettify(field.name)));
    const value = record[field.name];
    // Audit log "changes" — and any app-side blob using the same
    // {field: [before, after]} shape — gets a structured diff table
    // instead of the default JSON.stringify. Detection is by shape
    // so apps don't need a framework opt-in to use it.
    if (looksLikeAuditDiff(value)) {
      grid.appendChild(el("dd", {}, renderDiffTable(value)));
      continue;
    }
    // Reference label: show the resolved name with the raw id kept
    // alongside (muted) so the detail view stays traceable.
    const refLabel = record[`${field.name}__label`];
    if (refLabel != null) {
      grid.appendChild(
        el("dd", {}, [
          String(refLabel),
          value != null ? el("span", { class: "muted ref-raw" }, ` (${value})`) : null,
        ])
      );
      continue;
    }
    const formatted = formatValue(value, field);
    const dd = el("dd", { class: formatted.muted ? "muted" : "" }, formatted.text);
    if (formatted.mono) dd.style.fontFamily = "ui-monospace, SFMono-Regular, monospace";
    grid.appendChild(dd);
  }

  const caps = contract.capabilities || {};
  const actions = el("div", { class: "page-actions" }, [
    // Edit/Delete only when the contract says this caller may write — a
    // read-only resource (e.g. audit logs) reports update/delete=false, so
    // we don't offer controls the server would 403.
    caps.update === false
      ? null
      : el(
          "a",
          {
            class: "btn",
            href: `${cfg.uiPath}/${resource}/${encodeURIComponent(recordId)}/edit`,
          },
          "Edit"
        ),
    caps.delete === false
      ? null
      : el(
          "a",
          {
            class: "btn btn-danger",
            href: `${cfg.uiPath}/${resource}/${encodeURIComponent(recordId)}/delete`,
          },
          "Delete"
        ),
    // Tenant roles get a dedicated two-list permission picker.
    resource === "tenant_roles"
      ? el(
          "a",
          {
            class: "btn",
            href: `${cfg.uiPath}/${resource}/${encodeURIComponent(recordId)}/permissions`,
          },
          "Edit permissions"
        )
      : null,
    // A tenant can be "entered" as superadmin (scoped context switch).
    resource === "tenants"
      ? el("button", { type: "button", class: "btn", onClick: () => openTenant(recordId) }, "Open")
      : null,
  ]);

  mount(
    root,
    el("div", { class: "page-header" }, [el("h1", {}, `${contract.label} detail`), actions]),
    el("div", { class: "card" }, grid)
  );

  // Superadmin-only "Impersonate" button (users detail, gated by config).
  // Appended async after the page renders so the /auth/me check doesn't
  // block the detail view.
  renderImpersonateButton(resource, recordId)
    .then((btn) => {
      if (btn) actions.appendChild(btn);
    })
    .catch(() => {});
}

function errorScreen(message) {
  return el("div", { class: "card" }, el("p", { class: "form-error" }, message));
}

function prettify(name) {
  return String(name).replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}
