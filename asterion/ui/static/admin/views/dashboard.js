// Dashboard view: plain list of every registered resource, Django-style.

import { getFullContract } from "../contract.js";
import { el, mount, setBreadcrumb } from "../dom.js";

const cfg = window.ASTERION || {};

export async function mountDashboard(root) {
  setBreadcrumb([{ label: "Home" }]);

  const contract = await getFullContract();
  const models = (contract.models || []).slice().sort((a, b) =>
    a.label_plural.localeCompare(b.label_plural)
  );

  if (models.length === 0) {
    mount(
      root,
      el("div", { class: "page-header" }, el("h1", {}, "Site administration")),
      el(
        "div",
        { class: "card" },
        el("p", { class: "placeholder", style: "padding:1rem 1.5rem" }, "No admin models are registered yet.")
      )
    );
    return;
  }

  const items = models.map((m) =>
    el("li", {}, [
      el("a", { href: `${cfg.uiPath}/${m.resource}` }, [
        m.label_plural,
        m.description ? el("span", { class: "resource-desc" }, m.description) : null,
      ]),
    ])
  );

  mount(
    root,
    el("div", { class: "page-header" }, el("h1", {}, "Site administration")),
    el("div", { class: "card" }, el("ul", { class: "resource-list" }, items))
  );
}
