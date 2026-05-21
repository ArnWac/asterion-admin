// Detail view: read-only render of one record.

import { APIError, admin } from "../api.js";
import { getResourceContract } from "../contract.js";
import { el, mount, setBreadcrumb } from "../dom.js";
import { formatValue } from "../format.js";

const cfg = window.ADMINFOUNDRY || {};

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
    const formatted = formatValue(record[field.name], field);
    const dd = el("dd", { class: formatted.muted ? "muted" : "" }, formatted.text);
    if (formatted.mono) dd.style.fontFamily = "ui-monospace, SFMono-Regular, monospace";
    grid.appendChild(dd);
  }

  mount(
    root,
    el("div", { class: "page-header" }, [
      el("h1", {}, `${contract.label} detail`),
      el("div", { class: "page-actions" }, [
        el(
          "a",
          {
            class: "btn",
            href: `${cfg.uiPath}/${resource}/${encodeURIComponent(recordId)}/edit`,
          },
          "Edit"
        ),
        el(
          "a",
          {
            class: "btn btn-danger",
            href: `${cfg.uiPath}/${resource}/${encodeURIComponent(recordId)}/delete`,
          },
          "Delete"
        ),
      ]),
    ]),
    el("div", { class: "card" }, grid)
  );
}

function errorScreen(message) {
  return el("div", { class: "card" }, el("p", { class: "form-error" }, message));
}

function prettify(name) {
  return String(name).replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}
