// Value formatting helpers used by both the list table and the detail view.

export function formatValue(value, field) {
  if (value == null) return { text: "—", muted: true };

  const type = field?.type;
  if (type === "boolean") return { text: value ? "Yes" : "No" };
  if (type === "datetime") return { text: prettyDatetime(value) };
  if (type === "uuid") return { text: String(value), mono: true };

  if (typeof value === "object") {
    return { text: JSON.stringify(value), mono: true };
  }
  return { text: String(value) };
}

function prettyDatetime(iso) {
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return String(iso);
    return d.toLocaleString();
  } catch {
    return String(iso);
  }
}
