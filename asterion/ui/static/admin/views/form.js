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

  // Inline child tables (Theme C): render each declared inline as an editable
  // sub-table so one Edit on the parent writes the parent + all children in a
  // single transaction. Existing rows come from `existing.inlines[table]`
  // (attached by the read path). `collectInlines()` produces the `inlines`
  // payload block consumed by the server's inline writer.
  const inlineCollectors = [];
  for (const inlineMeta of contract.inlines || []) {
    const existingRows = (existing && existing.inlines && existing.inlines[inlineMeta.model]) || [];
    // Theme F: an inline declaring widget="dual_list" renders as a transfer
    // widget (available | assigned) over its value_field; everything else
    // keeps the editable add-row table.
    const section =
      inlineMeta.widget === "dual_list"
        ? buildInlineTransfer(inlineMeta, existingRows, resource)
        : buildInlineSection(inlineMeta, existingRows);
    form.appendChild(section.node);
    inlineCollectors.push(section);
  }
  function collectInlines() {
    const out = {};
    for (const c of inlineCollectors) {
      const rows = c.collect();
      if (rows.length) out[c.model] = rows;
    }
    return out;
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
    const inlinePayload = collectInlines();
    if (Object.keys(inlinePayload).length) payload.inlines = inlinePayload;
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

  // Foreign-key dropdowns: fetch the target resource's {value, label} options
  // and fill each FK <select>. Done after mount so the form renders instantly;
  // a failed/empty fetch leaves the provisional raw-id option in place.
  for (const field of editableFields) {
    if (field.widget !== "foreign_key") continue;
    const select = inputs.get(field.name);
    if (!select || select.tagName !== "SELECT" || select.disabled) continue;
    populateForeignKey(select, resource, field, existing ? existing[field.name] : null);
  }
}

async function populateForeignKey(select, resource, field, current) {
  let data;
  try {
    data = await admin.fieldOptions(resource, field.name);
  } catch {
    return; // keep the provisional/raw select on failure
  }
  const opts = (data && data.options) || [];
  // Nothing to offer (target not registered, or cross-schema FK not yet
  // supported): leave the provisional select alone so the value still shows.
  if (!opts.length) return;

  const currentStr = current != null ? String(current) : "";
  while (select.firstChild) select.removeChild(select.firstChild);
  if (field.nullable) select.appendChild(el("option", { value: "" }, "—"));

  let matched = false;
  for (const o of opts) {
    const val = String(o.value);
    const attrs = { value: val };
    if (val === currentStr) {
      attrs.selected = true;
      matched = true;
    }
    select.appendChild(el("option", attrs, o.label));
  }
  // Current value not in the (possibly truncated) page: keep it as a
  // provisional option so editing an unrelated field doesn't drop the FK.
  if (currentStr && !matched) {
    select.appendChild(
      el("option", { value: currentStr, selected: true }, `${currentStr} (current)`)
    );
  }
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

// Inline child table (Theme C). Returns { node, model, collect } — `collect`
// yields the row payloads (`{id, _delete}` / `{id, ...fields}` / `{...fields}`)
// for the server's inline writer. The inline contract carries field *names*
// only (no per-field type), so every column renders as a text input — enough
// for the built-in RBAC inlines (permission keys, membership ids).
function buildInlineSection(meta, existingRows) {
  const readonly = new Set(meta.readonly_fields || []);
  // Editable columns: declared fields minus the synthetic pk (sent as a hidden
  // id, not an editable column). The fk column is already excluded upstream.
  const columns = (meta.fields || []).filter((name) => name !== "id");
  const canDelete = meta.can_delete !== false;

  const tbody = el("tbody");
  const rowModels = [];

  function addRow(rowData) {
    const id = rowData && rowData.id != null ? rowData.id : null;
    const inputs = new Map();
    const cells = columns.map((name) => {
      const value = rowData ? rowData[name] : null;
      const input = el("input", {
        type: "text",
        value: value == null ? "" : String(value),
        disabled: readonly.has(name),
        "aria-label": prettify(name),
      });
      inputs.set(name, input);
      return el("td", {}, [input]);
    });

    let deleteCb = null;
    if (canDelete) {
      // New (unsaved) rows get a "remove" affordance that just drops the row
      // from the DOM; saved rows get a delete checkbox so the server removes
      // them on submit.
      if (id != null) {
        deleteCb = el("input", { type: "checkbox", "aria-label": "Delete row" });
        cells.push(el("td", { class: "inline-del" }, [deleteCb]));
      } else {
        cells.push(
          el("td", { class: "inline-del" }, [
            el(
              "button",
              {
                type: "button",
                class: "btn btn-sm btn-link",
                "aria-label": "Remove row",
                onClick: () => {
                  tr.remove();
                  const idx = rowModels.indexOf(model);
                  if (idx >= 0) rowModels.splice(idx, 1);
                  syncAddBtn();
                },
              },
              "✕"
            ),
          ])
        );
      }
    }

    const tr = el("tr", {}, cells);
    const model = { id, inputs, deleteCb };
    rowModels.push(model);
    tbody.appendChild(tr);
    syncAddBtn();
  }

  for (const row of existingRows) addRow(row);
  // Pre-render `extra` blank rows for new entries (skip in pure-readonly cases
  // where there are no editable columns).
  if (columns.length) {
    for (let i = 0; i < (meta.extra || 0); i += 1) addRow(null);
  }

  const addBtn = el(
    "button",
    { type: "button", class: "btn btn-sm", onClick: () => addRow(null) },
    "+ Add row"
  );
  function syncAddBtn() {
    if (meta.max_num == null) return;
    addBtn.disabled = rowModels.length >= meta.max_num;
  }
  syncAddBtn();

  const headCells = columns.map((name) => el("th", {}, prettify(name)));
  if (canDelete) headCells.push(el("th", { class: "inline-del" }, ""));

  const node = el("details", { class: "fieldset inline-section", open: true }, [
    el("summary", { class: "fieldset-legend" }, meta.label),
    el("table", { class: "inline-table" }, [
      el("thead", {}, el("tr", {}, headCells)),
      tbody,
    ]),
    columns.length ? el("div", { class: "inline-actions" }, [addBtn]) : null,
  ]);

  function collect() {
    const out = [];
    for (const m of rowModels) {
      if (m.id != null) {
        if (m.deleteCb && m.deleteCb.checked) {
          out.push({ id: m.id, _delete: true });
          continue;
        }
        const values = readRow(m, columns, readonly);
        out.push({ id: m.id, ...values });
      } else {
        const values = readRow(m, columns, readonly);
        // Skip wholly-empty blank rows so an untouched `extra` row isn't sent.
        if (Object.values(values).some((v) => v !== "" && v != null)) {
          out.push(values);
        }
      }
    }
    return out;
  }

  return { node, model: meta.model, collect };
}

// Dual-list / transfer inline (Theme F). Renders a Django-style two-list
// assign/unassign widget over the inline's single `value_field`, fed by the
// `_inline_options` endpoint (the universe of assignable values). Returns
// { node, model, collect } — `collect` diffs the assigned set against the
// existing rows and emits the same row payloads the add-row table does:
// `{ [value_field]: value }` for newly-assigned values and `{ id, _delete }`
// for removed ones. Unchanged assignments are omitted (the server's inline
// writer only touches rows present in the payload).
function buildInlineTransfer(meta, existingRows, resource) {
  const valueField = meta.value_field || (meta.fields && meta.fields[0]);

  // value -> existing row id (only rows already persisted carry an id).
  const existingByValue = new Map();
  for (const row of existingRows) {
    if (row && row[valueField] != null && row.id != null) {
      existingByValue.set(String(row[valueField]), row.id);
    }
  }

  const assigned = new Set(existingByValue.keys());
  const labels = new Map(); // value -> display label
  for (const v of existingByValue.keys()) labels.set(v, v);
  // Display order for the whole universe; seeded with existing values, then
  // replaced/extended once the option fetch resolves.
  let order = [...existingByValue.keys()];

  const availBox = el("select", {
    multiple: "",
    size: "12",
    class: "dual-list",
    "aria-label": `Available ${meta.label}`,
  });
  const assignedBox = el("select", {
    multiple: "",
    size: "12",
    class: "dual-list",
    "aria-label": `Assigned ${meta.label}`,
  });
  const availSearch = el("input", {
    type: "search",
    class: "dual-search",
    placeholder: "Filter…",
    "aria-label": `Filter available ${meta.label}`,
  });
  const assignedSearch = el("input", {
    type: "search",
    class: "dual-search",
    placeholder: "Filter…",
    "aria-label": `Filter assigned ${meta.label}`,
  });

  const labelFor = (v) => labels.get(v) || v;
  const matches = (v, needle) =>
    !needle || labelFor(v).toLowerCase().includes(needle) || v.toLowerCase().includes(needle);

  function rebuild() {
    const availNeedle = availSearch.value.trim().toLowerCase();
    const assignedNeedle = assignedSearch.value.trim().toLowerCase();
    availBox.replaceChildren(
      ...order
        .filter((v) => !assigned.has(v) && matches(v, availNeedle))
        .map((v) => el("option", { value: v }, labelFor(v)))
    );
    assignedBox.replaceChildren(
      ...order
        .filter((v) => assigned.has(v) && matches(v, assignedNeedle))
        .map((v) => el("option", { value: v }, labelFor(v)))
    );
  }

  function move(fromBox, add) {
    const values = [...fromBox.selectedOptions].map((o) => o.value);
    if (!values.length) return;
    for (const v of values) (add ? assigned.add(v) : assigned.delete(v));
    rebuild();
  }

  const addBtn = el("button", { type: "button", class: "btn" }, "Add →");
  const removeBtn = el("button", { type: "button", class: "btn" }, "← Remove");
  addBtn.addEventListener("click", () => move(availBox, true));
  removeBtn.addEventListener("click", () => move(assignedBox, false));
  availSearch.addEventListener("input", rebuild);
  assignedSearch.addEventListener("input", rebuild);

  rebuild();

  // Fetch the assignable universe after mount; a failure leaves the widget
  // in "remove-only" mode (existing assignments still render and can be
  // unassigned) rather than erroring the whole form.
  admin
    .inlineOptions(resource, meta.model)
    .then((data) => {
      const opts = (data && data.options) || [];
      const next = [];
      for (const o of opts) {
        const val = String(o.value);
        labels.set(val, o.label != null ? String(o.label) : val);
        next.push(val);
      }
      const seen = new Set(next);
      for (const v of existingByValue.keys()) if (!seen.has(v)) next.push(v);
      order = next;
      rebuild();
    })
    .catch(() => {
      /* keep remove-only — nothing to add */
    });

  const node = el("details", { class: "fieldset inline-section", open: true }, [
    el("summary", { class: "fieldset-legend" }, meta.label),
    el("div", { class: "dual-list-wrap" }, [
      el("div", { class: "dual-col" }, [el("label", {}, "Available"), availSearch, availBox]),
      el("div", { class: "dual-col dual-actions" }, [addBtn, removeBtn]),
      el("div", { class: "dual-col" }, [el("label", {}, "Assigned"), assignedSearch, assignedBox]),
    ]),
  ]);

  function collect() {
    const out = [];
    for (const v of assigned) {
      if (!existingByValue.has(v)) out.push({ [valueField]: v });
    }
    for (const [v, id] of existingByValue) {
      if (!assigned.has(v)) out.push({ id, _delete: true });
    }
    return out;
  }

  return { node, model: meta.model, collect };
}

function readRow(rowModel, columns, readonly) {
  const values = {};
  for (const name of columns) {
    if (readonly.has(name)) continue; // never send readonly columns
    const input = rowModel.inputs.get(name);
    if (input) values[name] = input.value;
  }
  return values;
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

  // Foreign-key picker: render a <select> that populateForeignKey() fills with
  // {value, label} options after mount. Start with a placeholder plus (when
  // editing) a provisional option for the current raw id, so the value is
  // preserved even before the options arrive / if the fetch fails.
  if (field.widget === "foreign_key") {
    const options = [];
    if (field.nullable) options.push(el("option", { value: "" }, "—"));
    if (initial != null && initial !== "") {
      options.push(el("option", { value: String(initial), selected: true }, String(initial)));
    }
    return el("select", baseAttrs, options);
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
