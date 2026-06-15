// Shared create + edit form, generated from the resource contract.
//
// On 422 we read the envelope's `fields[]` and surface a per-input error
// message right under the matching field.

import { APIError, admin } from "../api.js";
import { getResourceContract } from "../contract.js";
import { el, mount, setBreadcrumb, showToast } from "../dom.js";

const cfg = window.ADMINFOUNDRY || {};

export async function mountForm(root, resource, mode, recordId) {
  const isEdit = mode === "edit";
  const contract = await getResourceContract(resource);

  setBreadcrumb([
    { label: "Home", href: `${cfg.uiPath}/dashboard` },
    { label: contract.label_plural, href: `${cfg.uiPath}/${resource}` },
    isEdit
      ? { label: prettify(recordId), href: `${cfg.uiPath}/${resource}/${encodeURIComponent(recordId)}` }
      : { label: "New" },
    isEdit ? { label: "Edit" } : null,
  ].filter(Boolean));

  let existing = null;
  if (isEdit) {
    try {
      existing = await admin.read(resource, recordId);
    } catch (err) {
      const message = err instanceof APIError ? err.message : String(err);
      mount(root, errorScreen(message));
      return;
    }
  }

  const editableFields = contract.fields.filter((f) => !f.calculated && !f.hidden);

  const form = el("form", { class: "admin-form", novalidate: true });
  const inputs = new Map();
  const errorBoxes = new Map();

  for (const field of editableFields) {
    const disabled = isEdit ? field.read_only : field.read_only && !field.primary_key;
    // For create, we typically also want PKs hidden if they are server-generated
    // (UUID default, autoincrement). We *include* them but mark disabled when the
    // field is read_only — server fills them.
    const showOnCreate = !field.primary_key || field.read_only === false;
    if (!isEdit && !showOnCreate) continue;

    const id = `field-${field.name}`;
    const initial = existing ? existing[field.name] : null;
    const input = buildInput(field, id, initial, disabled);
    inputs.set(field.name, input);

    const errorBox = el("p", { class: "field-error", id: `${id}-error`, hidden: true });
    errorBoxes.set(field.name, errorBox);

    form.appendChild(
      el("div", { class: "field" }, [
        el("label", { for: id }, prettify(field.name) + (field.nullable ? "" : " *")),
        input,
        field.help_text ? el("p", { class: "field-hint" }, field.help_text) : null,
        field.read_only
          ? el("p", { class: "field-hint" }, "Read-only — managed by the server.")
          : null,
        errorBox,
      ])
    );
  }

  const summary = el("p", { class: "form-error", role: "alert", hidden: true });
  const submitBtn = el(
    "button",
    { type: "submit", class: "btn btn-primary" },
    isEdit ? "Save changes" : "Create"
  );
  const cancelHref = isEdit
    ? `${cfg.uiPath}/${resource}/${encodeURIComponent(recordId)}`
    : `${cfg.uiPath}/${resource}`;
  form.appendChild(
    el("div", { class: "form-actions" }, [
      submitBtn,
      el("a", { class: "btn btn-link", href: cancelHref }, "Cancel"),
    ])
  );
  form.appendChild(summary);

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    clearErrors(errorBoxes, summary);
    const payload = collectPayload(editableFields, inputs, isEdit);
    submitBtn.disabled = true;
    try {
      let result;
      if (isEdit) {
        result = await admin.update(resource, recordId, payload);
      } else {
        result = await admin.create(resource, payload);
      }
      const pkField = contract.fields.find((f) => f.primary_key);
      const newId = pkField ? result[pkField.name] : recordId;
      showToast(isEdit ? "Saved." : "Created.");
      window.location.href = `${cfg.uiPath}/${resource}/${encodeURIComponent(newId)}`;
    } catch (err) {
      submitBtn.disabled = false;
      if (err instanceof APIError) {
        applyErrors(err, errorBoxes, summary);
      } else {
        summary.textContent = String(err);
        summary.hidden = false;
      }
    }
  });

  mount(
    root,
    el("div", { class: "page-header" }, [
      el("h1", {}, `${isEdit ? "Edit" : "New"} ${contract.label.toLowerCase()}`),
    ]),
    el("div", { class: "card" }, form)
  );
}

function buildInput(field, id, initial, disabled) {
  const type = field.type;
  const baseAttrs = { id, name: field.name, disabled: disabled || false };
  // Placeholder (Roadmap 5.4) — harmless on input types that ignore it
  // (checkbox, datetime-local); only set when the contract supplies one.
  if (field.placeholder) baseAttrs.placeholder = field.placeholder;

  if (type === "boolean") {
    return el("input", {
      ...baseAttrs,
      type: "checkbox",
      checked: !!initial,
    });
  }

  if (type === "datetime") {
    return el("input", {
      ...baseAttrs,
      type: "datetime-local",
      value: toDatetimeLocal(initial),
    });
  }

  if (type === "integer" || type === "float") {
    return el("input", {
      ...baseAttrs,
      type: "number",
      step: type === "integer" ? "1" : "any",
      value: initial == null ? "" : String(initial),
    });
  }

  // string, uuid, fallback
  return el("input", {
    ...baseAttrs,
    type: "text",
    value: initial == null ? "" : String(initial),
  });
}

function collectPayload(fields, inputs, isEdit) {
  const payload = {};
  for (const field of fields) {
    const input = inputs.get(field.name);
    if (!input || input.disabled) continue;
    const value = readInputValue(input, field);
    if (!isEdit && value === null && !field.nullable) continue;
    payload[field.name] = value;
  }
  return payload;
}

function readInputValue(input, field) {
  if (field.type === "boolean") return !!input.checked;
  const raw = input.value;
  if (raw === "") return field.nullable ? null : "";
  if (field.type === "integer") {
    const n = parseInt(raw, 10);
    return Number.isNaN(n) ? raw : n;
  }
  if (field.type === "float") {
    const n = parseFloat(raw);
    return Number.isNaN(n) ? raw : n;
  }
  if (field.type === "datetime") {
    // datetime-local has no timezone — send as ISO with the local TZ offset
    // so the server stores the user's intent.
    return new Date(raw).toISOString();
  }
  return raw;
}

function clearErrors(errorBoxes, summary) {
  summary.hidden = true;
  summary.textContent = "";
  for (const box of errorBoxes.values()) {
    box.hidden = true;
    box.textContent = "";
  }
}

function applyErrors(err, errorBoxes, summary) {
  const fieldMap = err.fieldErrors();
  let placed = 0;
  for (const [name, message] of Object.entries(fieldMap)) {
    const box = errorBoxes.get(name);
    if (box) {
      box.textContent = message;
      box.hidden = false;
      placed += 1;
    }
  }
  if (placed === 0 || err.fields.length === 0) {
    summary.textContent = err.message;
    summary.hidden = false;
  }
}

function toDatetimeLocal(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const pad = (n) => String(n).padStart(2, "0");
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}` +
    `T${pad(d.getHours())}:${pad(d.getMinutes())}`
  );
}

function errorScreen(message) {
  return el("div", { class: "card" }, el("p", { class: "form-error" }, message));
}

function prettify(name) {
  return String(name).replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}
