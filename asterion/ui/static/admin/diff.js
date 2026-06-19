// Diff rendering for the audit log's "changes" blob (Roadmap 5.1b).
//
// The framework's audit publisher writes a flat dict of
// {field_name: [before, after]} into AuditLog.changes. The detail
// view's default JSON render turns that into an unreadable one-line
// JSON.stringify; this module replaces it with a small two-column
// table so the diff is glanceable.
//
// Detection is structural — there's no widget hint flowing through
// the contract for "this is an audit diff." The shape is the source
// of truth: any non-null plain object whose every value is a
// 2-element array. That same shape lets app-side audit publishers
// reuse the renderer without coordinating with the framework.

import { el } from "./dom.js";
import { formatValue } from "./format.js";

const DASH = "—";

export function looksLikeAuditDiff(value) {
  if (value == null || typeof value !== "object" || Array.isArray(value)) {
    return false;
  }
  const entries = Object.entries(value);
  if (entries.length === 0) return false;
  for (const [, change] of entries) {
    if (!Array.isArray(change) || change.length !== 2) return false;
  }
  return true;
}

function _renderSide(value) {
  // Reuse format.js so dates/booleans/UUIDs print the same way they
  // do in the detail grid — keeps the diff cells visually consistent
  // with the surrounding fields.
  if (value == null) return el("span", { class: "muted" }, DASH);
  const formatted = formatValue(value, { type: typeof value === "boolean" ? "boolean" : null });
  const span = el("span", {}, formatted.text);
  if (formatted.mono) span.style.fontFamily = "ui-monospace, SFMono-Regular, monospace";
  return span;
}

function _rowClass(before, after) {
  if (before == null && after != null) return "diff-row-added";
  if (before != null && after == null) return "diff-row-removed";
  return "diff-row-changed";
}

export function renderDiffTable(value) {
  const rows = [];
  for (const [name, change] of Object.entries(value)) {
    const [before, after] = change;
    rows.push(
      el("tr", { class: _rowClass(before, after) }, [
        el("th", { scope: "row" }, name),
        el("td", { class: "diff-cell diff-before" }, _renderSide(before)),
        el("td", { class: "diff-arrow", "aria-hidden": "true" }, "→"),
        el("td", { class: "diff-cell diff-after" }, _renderSide(after)),
      ])
    );
  }

  const thead = el("thead", {}, el("tr", {}, [
    el("th", { scope: "col" }, "Field"),
    el("th", { scope: "col" }, "Before"),
    el("th", { scope: "col", "aria-hidden": "true" }, ""),
    el("th", { scope: "col" }, "After"),
  ]));

  return el(
    "table",
    { class: "diff-table" },
    thead,
    el("tbody", {}, ...rows)
  );
}
