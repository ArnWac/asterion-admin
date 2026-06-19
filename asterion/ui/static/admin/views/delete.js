// Delete confirmation page.
//
// Shows a small summary of the record so the user knows what they're about
// to remove, then sends DELETE on confirm.

import { APIError, admin } from "../api.js";
import { getResourceContract } from "../contract.js";
import { el, mount, setBreadcrumb, showToast } from "../dom.js";
import { formatValue } from "../format.js";

const cfg = window.ASTERION || {};

export async function mountDelete(root, resource, recordId) {
  const contract = await getResourceContract(resource);
  setBreadcrumb([
    { label: "Home", href: `${cfg.uiPath}/dashboard` },
    { label: contract.label_plural, href: `${cfg.uiPath}/${resource}` },
    { label: prettify(recordId), href: `${cfg.uiPath}/${resource}/${encodeURIComponent(recordId)}` },
    { label: "Delete" },
  ]);

  let record;
  try {
    record = await admin.read(resource, recordId);
  } catch (err) {
    const message = err instanceof APIError ? err.message : String(err);
    mount(root, errorScreen(message));
    return;
  }

  const summary = el("dl", { class: "detail-grid" });
  for (const field of contract.fields.slice(0, 5)) {
    summary.appendChild(el("dt", {}, prettify(field.name)));
    const formatted = formatValue(record[field.name], field);
    summary.appendChild(el("dd", { class: formatted.muted ? "muted" : "" }, formatted.text));
  }

  const errorBox = el("p", { class: "form-error", hidden: true });
  const confirmBtn = el("button", { class: "btn btn-danger" }, "Delete permanently");
  const cancelLink = el(
    "a",
    {
      class: "btn btn-link",
      href: `${cfg.uiPath}/${resource}/${encodeURIComponent(recordId)}`,
    },
    "Cancel"
  );

  confirmBtn.addEventListener("click", async () => {
    errorBox.hidden = true;
    confirmBtn.disabled = true;
    try {
      await admin.remove(resource, recordId);
      showToast(`${contract.label} deleted.`);
      window.location.href = `${cfg.uiPath}/${resource}`;
    } catch (err) {
      confirmBtn.disabled = false;
      const message = err instanceof APIError ? err.message : String(err);
      errorBox.textContent = message;
      errorBox.hidden = false;
    }
  });

  mount(
    root,
    el("div", { class: "page-header" }, [
      el("h1", {}, `Delete ${contract.label.toLowerCase()}?`),
    ]),
    el("div", { class: "card" }, [
      el("div", { style: "padding:1rem 1.5rem" }, [
        el("p", { class: "muted", style: "margin:0 0 1rem" }, "Review the record before confirming. This action cannot be undone."),
        summary,
        el("div", { class: "danger-zone" }, [
          el("p", { class: "warning-text" }, [
            el("strong", {}, "Warning: "),
            "this will permanently delete the record.",
          ]),
          errorBox,
          el("div", { style: "display:flex;gap:.5rem" }, [confirmBtn, cancelLink]),
        ]),
      ]),
    ])
  );
}

function errorScreen(message) {
  return el("div", { class: "card" }, el("p", { class: "form-error" }, message));
}

function prettify(name) {
  return String(name).replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}
