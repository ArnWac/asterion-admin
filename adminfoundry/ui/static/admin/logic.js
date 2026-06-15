// Pure, DOM-free UI logic (Roadmap stabilization P1).
//
// Extracted from the view modules so it can be unit-tested without a
// browser. Keep this file free of DOM access and imports — every export
// must be a pure function of its arguments.

export function looseEqual(a, b) {
  // Form inputs normalize to string/number/boolean; tolerate the
  // string/number coercion (e.g. select value "2" vs rule value 2) while
  // keeping booleans and exact matches strict.
  if (a === b) return true;
  if (a == null || b == null) return false;
  return String(a) === String(b);
}

// Conditional-field visibility (Roadmap 5.4): is this field visible given
// the controlling field's current value?
export function conditionSatisfied(condition, value) {
  if (!condition) return true;
  if ("equals" in condition) return looseEqual(value, condition.equals);
  if ("in" in condition && Array.isArray(condition.in)) {
    return condition.in.some((v) => looseEqual(v, value));
  }
  return true; // unknown rule shape → don't hide
}

// Dependent-field choices (Roadmap 5.4): which options are allowed for the
// controlling field's current value?
export function allowedDependencyOptions(dependency, controllingValue) {
  if (!dependency || !dependency.options) return [];
  return dependency.options[String(controllingValue)] || [];
}

// Sortable column cycle (Roadmap 5.5): ascending → descending → off.
export function nextSortState(current, column) {
  if (current === column) return `-${column}`;
  if (current === `-${column}`) return null;
  return column;
}

// Date hierarchy (Roadmap 5.5): compose a ?dh= value from the selected
// year/month/day levels. Returns null when no year is chosen; month/day
// are only appended when the level above is present.
export function composeDateHierarchy(year, month, day) {
  const y = String(year == null ? "" : year).trim();
  if (!y) return null;
  const pad2 = (n) => String(n).padStart(2, "0");
  if (!month) return y;
  let value = `${y}-${pad2(month)}`;
  if (day) value += `-${pad2(day)}`;
  return value;
}

// Inline edit (Roadmap 5.5): has a cell value changed from its original?
export function editIsDirty(value, original) {
  return String(value) !== String(original == null ? "" : original);
}
