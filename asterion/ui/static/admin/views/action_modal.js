// Action input modal (Theme D) — used by list.js when a row/bulk action
// declares an `input_schema`. Renders a small form from the action's JSON
// schema (pydantic `model_json_schema()` output), collects the values, and
// hands them to an async `submit(data)` callback. The modal stays open and
// shows field/summary errors when `submit` throws an APIError, closing only
// on success — same contract as the import modal.

import { APIError } from "../api.js";
import { clear, el } from "../dom.js";

/**
 * @param {object} opts
 * @param {string} opts.title       Dialog heading (the action label).
 * @param {object} opts.schema      JSON schema for the action input.
 * @param {(data: object) => Promise<any>} opts.submit  Performs the action.
 * @param {(result: any) => void} [opts.onDone]  Called with the result on success.
 */
export function openActionModal({ title, schema, submit, onDone }) {
  const overlay = el("div", { class: "modal-overlay", role: "dialog", "aria-modal": "true" });
  const box = el("div", { class: "modal-box" });
  overlay.appendChild(box);

  const props = (schema && schema.properties) || {};
  const required = new Set((schema && schema.required) || []);
  const inputs = new Map();
  const errorBoxes = new Map();

  const fieldNodes = Object.entries(props).map(([name, spec]) => {
    const id = `action-field-${name}`;
    const input = buildInput(name, spec, id);
    inputs.set(name, input);
    const errorBox = el("p", { class: "field-error", id: `${id}-error`, hidden: true });
    errorBoxes.set(name, errorBox);
    const label = spec.title || prettify(name);
    return el("div", { class: "field" }, [
      el("label", { for: id }, label + (required.has(name) ? " *" : "")),
      input,
      spec.description ? el("p", { class: "field-hint" }, spec.description) : null,
      errorBox,
    ]);
  });

  const summary = el("p", { class: "form-error", role: "alert", hidden: true });
  const submitBtn = el("button", { type: "submit", class: "btn btn-primary" }, "Run");
  const cancelBtn = el("button", { type: "button", class: "btn btn-link" }, "Cancel");

  const form = el("form", { novalidate: true, "aria-label": title }, [
    el("h2", {}, title),
    ...fieldNodes,
    summary,
    el("div", { class: "form-actions" }, [submitBtn, cancelBtn]),
  ]);
  box.appendChild(form);

  function close() {
    overlay.remove();
    document.removeEventListener("keydown", onEsc);
  }
  function onEsc(e) {
    if (e.key === "Escape") close();
  }
  cancelBtn.addEventListener("click", close);
  document.addEventListener("keydown", onEsc);

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    clearErrors(errorBoxes, summary);
    const data = collect(props, inputs);
    submitBtn.disabled = true;
    try {
      const result = await submit(data);
      close();
      if (typeof onDone === "function") onDone(result);
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

  document.body.appendChild(overlay);
  const first = box.querySelector("input, select, textarea");
  if (first) first.focus();
}

// --- schema → input ---

function schemaType(spec) {
  if (spec.type) return spec.type;
  // Optionals serialize as anyOf: [{type: X}, {type: "null"}].
  if (Array.isArray(spec.anyOf)) {
    const real = spec.anyOf.find((s) => s.type && s.type !== "null");
    if (real) return real.type;
  }
  return "string";
}

// Theme G: the schema's `format` drives the widget for string fields.
// pydantic emits `date-time` / `date` / `time` for datetime/date/time
// fields, `uuid` for UUIDs. Optionals carry the format on the non-null
// branch of `anyOf`, so look there too.
function schemaFormat(spec) {
  if (spec.format) return spec.format;
  if (Array.isArray(spec.anyOf)) {
    const real = spec.anyOf.find((s) => s.format && s.type !== "null");
    if (real) return real.format;
  }
  return null;
}

// String validation hints (Theme G) — pydantic surfaces these on the spec
// (directly or on the non-null anyOf branch). Mapped onto the input so the
// browser flags bad values before the round-trip.
function schemaConstraints(spec) {
  const src =
    (Array.isArray(spec.anyOf) && spec.anyOf.find((s) => s.type && s.type !== "null")) || spec;
  const out = {};
  if (src.minLength != null) out.minlength = String(src.minLength);
  if (src.maxLength != null) out.maxlength = String(src.maxLength);
  if (src.pattern != null) out.pattern = src.pattern;
  if (src.minimum != null) out.min = String(src.minimum);
  if (src.maximum != null) out.max = String(src.maximum);
  return out;
}

// HTML input `type` for a string format (Theme G). Returns null for formats
// with no native widget (e.g. uuid) so the caller falls back to text.
function inputTypeForFormat(format) {
  if (format === "date-time") return "datetime-local";
  if (format === "date") return "date";
  if (format === "time") return "time";
  return null;
}

function buildInput(name, spec, id) {
  const enumValues = spec.enum || (Array.isArray(spec.anyOf) && spec.anyOf.find((s) => s.enum)?.enum);
  if (Array.isArray(enumValues)) {
    return el(
      "select",
      { id, name },
      enumValues.map((v) => el("option", { value: String(v) }, String(v)))
    );
  }
  const type = schemaType(spec);
  if (type === "boolean") {
    return el("input", { id, name, type: "checkbox", checked: !!spec.default });
  }
  if (type === "integer" || type === "number") {
    return el("input", {
      id,
      name,
      type: "number",
      step: type === "integer" ? "1" : "any",
      ...schemaConstraints(spec),
      value: spec.default != null ? String(spec.default) : "",
    });
  }
  // String: a recognised `format` picks a native date/time widget; otherwise
  // a plain text input carrying any pattern/length validation hints.
  const fmtType = inputTypeForFormat(schemaFormat(spec));
  if (fmtType) {
    return el("input", {
      id,
      name,
      type: fmtType,
      value: fmtType === "datetime-local" ? toDatetimeLocal(spec.default) : spec.default || "",
    });
  }
  return el("input", {
    id,
    name,
    type: "text",
    ...schemaConstraints(spec),
    value: spec.default != null ? String(spec.default) : "",
  });
}

function collect(props, inputs) {
  const data = {};
  for (const [name, spec] of Object.entries(props)) {
    const input = inputs.get(name);
    if (!input) continue;
    const type = schemaType(spec);
    if (type === "boolean") {
      data[name] = !!input.checked;
      continue;
    }
    const raw = input.value;
    if (raw === "") continue; // omit empty optionals
    if (type === "integer") {
      const n = parseInt(raw, 10);
      data[name] = Number.isNaN(n) ? raw : n;
    } else if (type === "number") {
      const n = parseFloat(raw);
      data[name] = Number.isNaN(n) ? raw : n;
    } else if (schemaFormat(spec) === "date-time") {
      // datetime-local has no timezone — send ISO with the local offset so
      // the server stores the user's intended instant.
      const d = new Date(raw);
      data[name] = Number.isNaN(d.getTime()) ? raw : d.toISOString();
    } else {
      data[name] = raw;
    }
  }
  return data;
}

// Format an ISO/parseable timestamp for a `datetime-local` input's value
// (which wants `YYYY-MM-DDTHH:mm` in local time, no timezone suffix).
function toDatetimeLocal(value) {
  if (!value) return "";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return "";
  const pad = (n) => String(n).padStart(2, "0");
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}` +
    `T${pad(d.getHours())}:${pad(d.getMinutes())}`
  );
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
  if (placed === 0) {
    summary.textContent = err.message;
    summary.hidden = false;
  }
}

function prettify(name) {
  return String(name).replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}
