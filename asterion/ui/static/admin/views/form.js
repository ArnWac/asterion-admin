// Shared create + edit form, generated from the resource contract.
//
// On 422 we read the envelope's `fields[]` and surface a per-input error
// message right under the matching field.

import { APIError, admin } from "../api.js";
import { getResourceContract } from "../contract.js";
import { el, mount, setBreadcrumb, showToast } from "../dom.js";
import { allowedDependencyOptions, conditionSatisfied } from "../logic.js";

const cfg = window.ASTERION || {};

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
  const fieldDivs = new Map(); // field name -> rendered .field block
  const renderedOrder = []; // field names actually rendered, in contract order

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

    fieldDivs.set(
      field.name,
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
    renderedOrder.push(field.name);
  }

  // Lay the rendered fields out: grouped into sections when the contract
  // declares fieldsets (Roadmap 5.4), flat otherwise. Fields not named in
  // any fieldset are appended after the sections so nothing is dropped.
  for (const node of buildFormBody(
    contract.fieldsets || [],
    fieldDivs,
    renderedOrder,
    contract.form_layout || "sections"
  )) {
    form.appendChild(node);
  }

  // Conditional fields (Roadmap 5.4): show/hide a dependent field based on
  // another field's live value, and exclude hidden fields from the payload
  // so the server never receives a value the user couldn't see.
  const fieldByName = new Map(editableFields.map((f) => [f.name, f]));
  const hiddenByCondition = new Set();
  const conditionalFields = editableFields.filter(
    (f) => f.condition && fieldDivs.has(f.name)
  );

  function evaluateConditions() {
    for (const f of conditionalFields) {
      const refInput = inputs.get(f.condition.field);
      const refField = fieldByName.get(f.condition.field);
      const refValue =
        refInput && refField ? readInputValue(refInput, refField) : null;
      const visible = conditionSatisfied(f.condition, refValue);
      const container = fieldDivs.get(f.name);
      if (container) container.hidden = !visible;
      if (visible) hiddenByCondition.delete(f.name);
      else hiddenByCondition.add(f.name);
    }
  }

  if (conditionalFields.length) {
    for (const refName of new Set(conditionalFields.map((f) => f.condition.field))) {
      const refInput = inputs.get(refName);
      if (!refInput) continue;
      refInput.addEventListener("input", evaluateConditions);
      refInput.addEventListener("change", evaluateConditions);
    }
    evaluateConditions();
  }

  // Dependent fields (Roadmap 5.4): narrow a dependent <select>'s options
  // to those allowed for the controlling field's current value.
  const dependentFields = editableFields.filter(
    (f) => f.dependency && inputs.get(f.name) && inputs.get(f.name).tagName === "SELECT"
  );

  function evaluateDependencies() {
    for (const f of dependentFields) {
      const select = inputs.get(f.name);
      const ctrlInput = inputs.get(f.dependency.field);
      const ctrlField = fieldByName.get(f.dependency.field);
      const ctrlValue =
        ctrlInput && ctrlField ? readInputValue(ctrlInput, ctrlField) : null;
      const allowed = allowedDependencyOptions(f.dependency, ctrlValue);
      rebuildSelectOptions(select, f, allowed);
    }
  }

  if (dependentFields.length) {
    for (const ctrlName of new Set(dependentFields.map((f) => f.dependency.field))) {
      const ctrlInput = inputs.get(ctrlName);
      if (!ctrlInput) continue;
      ctrlInput.addEventListener("input", evaluateDependencies);
      ctrlInput.addEventListener("change", evaluateDependencies);
    }
    evaluateDependencies();
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
    const payload = collectPayload(editableFields, inputs, isEdit, hiddenByCondition);
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

function buildFormBody(fieldsets, fieldDivs, renderedOrder, layout) {
  // No fieldsets declared → flat list in contract order (legacy layout).
  if (!fieldsets.length) {
    return renderedOrder.map((name) => fieldDivs.get(name)).filter(Boolean);
  }

  // Collect each fieldset's rendered nodes once, tracking what's placed so
  // leftovers (fields in no fieldset) can be appended without duplication.
  const groups = []; // { label, description, collapsed, nodes }
  const placed = new Set();
  for (const fs of fieldsets) {
    const fieldNodes = (fs.fields || [])
      .filter((name) => fieldDivs.has(name) && !placed.has(name))
      .map((name) => {
        placed.add(name);
        return fieldDivs.get(name);
      });
    if (fieldNodes.length === 0) continue; // skip empty section
    groups.push({
      label: fs.label,
      description: fs.description,
      collapsed: !!fs.collapsed,
      nodes: fieldNodes,
    });
  }

  const leftovers = renderedOrder
    .filter((name) => !placed.has(name))
    .map((name) => fieldDivs.get(name))
    .filter(Boolean);

  if (layout === "tabs") {
    return buildTabs(groups, leftovers);
  }

  // "sections" (default): collapsible <details> blocks + loose leftovers.
  const nodes = groups.map((g) => buildSection(g.label, g.description, g.collapsed, g.nodes));
  nodes.push(...leftovers);
  return nodes;
}

function buildTabs(groups, leftovers) {
  const tabs = groups.map((g) => {
    const panelChildren = [];
    if (g.description) panelChildren.push(el("p", { class: "fieldset-description" }, g.description));
    panelChildren.push(...g.nodes);
    return { label: g.label, panel: el("div", { class: "tab-panel" }, panelChildren) };
  });
  if (leftovers.length) {
    tabs.push({ label: "Other", panel: el("div", { class: "tab-panel" }, leftovers) });
  }
  if (!tabs.length) return [];

  const tablist = el("div", { class: "tabs", role: "tablist" });
  const panelsWrap = el("div", { class: "tab-panels" });

  tabs.forEach((t, i) => {
    const btn = el(
      "button",
      {
        type: "button",
        class: "tab" + (i === 0 ? " active" : ""),
        role: "tab",
        "aria-selected": i === 0 ? "true" : "false",
      },
      t.label
    );
    t.panel.hidden = i !== 0;
    btn.addEventListener("click", () => {
      for (const b of tablist.children) {
        b.classList.remove("active");
        b.setAttribute("aria-selected", "false");
      }
      for (const p of panelsWrap.children) p.hidden = true;
      btn.classList.add("active");
      btn.setAttribute("aria-selected", "true");
      t.panel.hidden = false;
    });
    tablist.appendChild(btn);
    panelsWrap.appendChild(t.panel);
  });

  return [tablist, panelsWrap];
}

function buildSection(label, description, collapsed, fieldNodes) {
  // <details> gives a native collapse affordance; open unless collapsed.
  const children = [el("summary", { class: "fieldset-legend" }, label)];
  if (description) children.push(el("p", { class: "fieldset-description" }, description));
  children.push(...fieldNodes);
  return el("details", { class: "fieldset", open: !collapsed }, children);
}

function rebuildSelectOptions(select, field, allowed) {
  // Replace a dependent select's options with the allowed set, preserving
  // the current selection when it's still valid (else it falls back to the
  // first option — the empty placeholder for nullable fields).
  const current = select.value;
  while (select.firstChild) select.removeChild(select.firstChild);
  if (field.nullable) select.appendChild(el("option", { value: "" }, "—"));
  for (const choice of allowed) {
    const val = String(choice);
    const attrs = { value: val };
    if (val === current) attrs.selected = true;
    select.appendChild(el("option", attrs, val));
  }
}

function buildInput(field, id, initial, disabled) {
  const type = field.type;
  const baseAttrs = { id, name: field.name, disabled: disabled || false };
  // Placeholder (Roadmap 5.4) — harmless on input types that ignore it
  // (checkbox, datetime-local); only set when the contract supplies one.
  if (field.placeholder) baseAttrs.placeholder = field.placeholder;

  // Widget-driven inputs (Roadmap 5.4): the contract has carried
  // widget + choices since A4, but the form ignored them. Honour the
  // two built-in widgets here; custom widgets are handled by the registry
  // in mountForm before this function is reached.
  const choices = (field.metadata && field.metadata.choices) || null;
  if (field.widget === "select" && Array.isArray(choices)) {
    const options = [];
    if (field.nullable) options.push(el("option", { value: "" }, "—"));
    for (const choice of choices) {
      const val = String(choice);
      const attrs = { value: val };
      if (initial != null && String(initial) === val) attrs.selected = true;
      options.push(el("option", attrs, val));
    }
    return el("select", baseAttrs, options);
  }
  if (field.widget === "textarea") {
    return el("textarea", { ...baseAttrs, rows: 4 }, initial == null ? "" : String(initial));
  }

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

function collectPayload(fields, inputs, isEdit, hiddenSet) {
  const payload = {};
  for (const field of fields) {
    // A field hidden by a conditional rule submits no value.
    if (hiddenSet && hiddenSet.has(field.name)) continue;
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
