// Tiny inline-SVG icon set for the list-view row action bar (Theme D).
//
// `AdminAction.icon` is a free-form string; we map a small set of known
// names to a glyph and fall back to a generic "action" bolt for anything
// unrecognised, so a custom icon name never breaks rendering.

const PATHS = {
  // 24x24 viewBox, stroke-based so they inherit `currentColor`.
  eye: "M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7z M12 9a3 3 0 100 6 3 3 0 000-6z",
  trash: "M3 6h18 M8 6V4a1 1 0 011-1h6a1 1 0 011 1v2 M19 6l-1 14a2 2 0 01-2 2H8a2 2 0 01-2-2L5 6 M10 11v6 M14 11v6",
  pencil: "M12 20h9 M16.5 3.5a2.1 2.1 0 013 3L7 19l-4 1 1-4 12.5-12.5z",
  clock: "M12 22a10 10 0 100-20 10 10 0 000 20z M12 6v6l4 2",
  check: "M20 6L9 17l-5-5",
  // Generic fallback (a lightning bolt) for unmapped action icons.
  action: "M13 2L3 14h7l-1 8 10-12h-7l1-8z",
};

/**
 * Build an inline SVG element for the given icon name. Unknown names get
 * the generic `action` glyph. The SVG inherits text color and sizes to 1em
 * so it lines up with adjacent text/buttons.
 */
export function icon(name) {
  const d = PATHS[name] || PATHS.action;
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("width", "16");
  svg.setAttribute("height", "16");
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "2");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  svg.setAttribute("aria-hidden", "true");
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("d", d);
  svg.appendChild(path);
  return svg;
}
