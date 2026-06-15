// Import modal — used by list.js when the user clicks "Import".
//
// Behaviour:
//   * Opens an overlay with a file picker (accept .csv,.xlsx).
//   * Submits the file to /{resource}/_import via FormData.
//   * Renders the JSON import report inline (created / failed / errors).
//   * Calls onDone() after Close so the list view can reload.
//
// Lives in its own module to keep list.js focused. The endpoint only
// exists when the import_export extension is mounted server-side; a 404
// surfaces as a clear toast.

import { APIError, admin } from "../api.js";
import { clear, el, showToast } from "../dom.js";

export function openImportModal(resource, contract, onDone, capability) {
  const overlay = el("div", { class: "modal-overlay", role: "dialog", "aria-modal": "true" });
  const box = el("div", { class: "modal-box" });
  overlay.appendChild(box);

  // Restrict the picker to the formats this install can actually parse.
  // Falls back to both when the capability is absent (older callers).
  const formats = (capability && capability.import_formats) || ["csv", "xlsx"];
  const accept = formats.map((f) => `.${f}`).join(",");
  const acceptLabel = formats.map((f) => f.toUpperCase()).join(" or ");

  const fileInput = el("input", {
    type: "file",
    name: "file",
    accept,
    "aria-label": `Choose ${acceptLabel} file`,
  });
  const submitBtn = el("button", { type: "submit", class: "btn btn-primary" }, "Upload");
  const cancelBtn = el("button", { type: "button", class: "btn btn-link" }, "Cancel");
  const status = el("div", { class: "modal-status", "aria-live": "polite" });

  const form = el(
    "form",
    { novalidate: true, "aria-label": `Import ${contract.label_plural}` },
    [
      el("h2", {}, `Import ${contract.label_plural}`),
      el(
        "p",
        { class: "muted" },
        "Upload a CSV or XLSX file. Each row creates one new record."
      ),
      el("div", { class: "field" }, [fileInput]),
      status,
      el("div", { class: "form-actions" }, [submitBtn, cancelBtn]),
    ]
  );
  box.appendChild(form);

  function close() {
    overlay.remove();
    document.removeEventListener("keydown", onEsc);
    if (typeof onDone === "function") onDone();
  }

  function onEsc(e) {
    if (e.key === "Escape") close();
  }

  cancelBtn.addEventListener("click", close);
  document.addEventListener("keydown", onEsc);

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const file = fileInput.files && fileInput.files[0];
    if (!file) {
      renderStatus(status, "Choose a file first.", "error");
      return;
    }
    submitBtn.disabled = true;
    renderStatus(status, "Uploading…", "info");
    try {
      const report = await admin.importFile(resource, file);
      renderReport(status, report);
      submitBtn.remove();
      cancelBtn.textContent = "Close";
      if (typeof onDone === "function") {
        // Refresh the list immediately to show inserted rows.
        try { onDone(); } catch { /* no-op */ }
      }
    } catch (err) {
      submitBtn.disabled = false;
      const message = err instanceof APIError ? err.message : String(err);
      renderStatus(status, message, "error");
      showToast(`Import failed: ${message}`, { type: "error" });
    }
  });

  document.body.appendChild(overlay);
  fileInput.focus();
}

function renderStatus(node, message, kind) {
  clear(node);
  const cls = kind === "error" ? "form-error" : "field-hint";
  node.appendChild(el("p", { class: cls }, message));
}

function renderReport(node, report) {
  clear(node);
  const created = report.created ?? 0;
  const failed = report.failed ?? 0;
  const errors = Array.isArray(report.errors) ? report.errors : [];

  node.appendChild(
    el("p", {}, `Created ${created} record(s), ${failed} failed.`)
  );

  if (errors.length > 0) {
    const list = el("ul", { class: "import-errors" });
    for (const e of errors.slice(0, 50)) {
      const row = typeof e.row === "number" ? `Row ${e.row}: ` : "";
      list.appendChild(el("li", {}, `${row}${e.error || "Unknown error"}`));
    }
    if (errors.length > 50) {
      list.appendChild(
        el("li", { class: "muted" }, `… and ${errors.length - 50} more.`)
      );
    }
    node.appendChild(list);
  }
}
