// Settings placeholder.
//
// The route exists in the UI router for navigational symmetry, but no
// settings endpoints are exposed by the current backend. We render a
// short notice so the link is not a dead end.

import { auth } from "../api.js";
import { el, mount, setBreadcrumb } from "../dom.js";

const cfg = window.ASTERION || {};

export async function mountSettings(root) {
  setBreadcrumb([
    { label: "Home", href: `${cfg.uiPath}/dashboard` },
    { label: "Settings" },
  ]);

  let me = null;
  try {
    me = await auth.me();
  } catch {
    /* fall through — message is informational */
  }

  const rows = [];
  if (me) {
    rows.push(el("dt", {}, "Signed in as"));
    rows.push(el("dd", {}, me.email || "—"));
    if (me.id) {
      rows.push(el("dt", {}, "User ID"));
      rows.push(el("dd", { class: "muted" }, String(me.id)));
    }
  }

  mount(
    root,
    el("div", { class: "page-header" }, el("h1", {}, "Settings")),
    el("div", { class: "card" }, [
      el(
        "p",
        { style: "padding:1rem 1.5rem;margin:0" },
        "There are no configurable settings exposed in this version. Account-level changes are managed by your administrator."
      ),
      rows.length ? el("dl", { class: "detail-grid" }, rows) : null,
    ])
  );
}
