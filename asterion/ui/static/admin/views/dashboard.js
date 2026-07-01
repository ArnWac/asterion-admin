// Dashboard view: plain list of every registered resource, Django-style.

import { getFullContract } from "../contract.js";
import { el, mount, setBreadcrumb } from "../dom.js";
import { groupSidebarModels } from "../logic.js";

const cfg = window.ASTERION || {};

function _resourceItem(m) {
  return el("li", {}, [
    el("a", { href: `${cfg.uiPath}/${m.resource}` }, [
      m.label_plural,
      m.description ? el("span", { class: "resource-desc" }, m.description) : null,
    ]),
  ]);
}

export async function mountDashboard(root) {
  setBreadcrumb([{ label: "Home" }]);

  const contract = await getFullContract();
  // Group by the same categories/order as the sidebar (Roadmap 5.7):
  // config order → alphabetical → "System" last, sorted within each group by
  // nav_order then label_plural. groupSidebarModels also applies the
  // show_in_nav filter, so we don't repeat it here.
  const { ungrouped, groups } = groupSidebarModels(
    contract.models || [],
    contract.sidebar_categories || [],
  );

  if (ungrouped.length === 0 && groups.length === 0) {
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

  const cardChildren = [];
  if (ungrouped.length > 0) {
    cardChildren.push(el("ul", { class: "resource-list" }, ungrouped.map(_resourceItem)));
  }
  for (const group of groups) {
    cardChildren.push(el("h2", { class: "resource-category" }, group.category));
    cardChildren.push(el("ul", { class: "resource-list" }, group.models.map(_resourceItem)));
  }

  mount(
    root,
    el("div", { class: "page-header" }, el("h1", {}, "Site administration")),
    el("div", { class: "card" }, cardChildren)
  );
}
