// Custom Admin Page host (Roadmap 5.6).
//
// The server serves {uiPath}/_pages/{id} with view="page" and injects the
// page's `js_module` URL into window.ASTERION.pageModule. This host
// dynamically imports that module and hands it the #app-root element plus
// a small context object. The page module is app/extension-supplied and
// trusted (it was registered server-side), so importing its URL is safe.
//
// A page module must export either a `mount(root, ctx)` function or a
// default export of the same shape. `ctx` currently carries { pageId }.

import { el, mount } from "../dom.js";

export async function mountPage(root, moduleUrl, pageId) {
  if (!moduleUrl) {
    mount(
      root,
      el("div", { class: "card" }, el("p", { class: "form-error" }, "This page has no module configured."))
    );
    return;
  }

  let mod;
  try {
    mod = await import(/* @vite-ignore */ moduleUrl);
  } catch (err) {
    mount(
      root,
      el(
        "div",
        { class: "card" },
        el("p", { class: "form-error" }, `Failed to load page module: ${String(err)}`)
      )
    );
    return;
  }

  const fn = typeof mod.mount === "function" ? mod.mount : mod.default;
  if (typeof fn !== "function") {
    mount(
      root,
      el(
        "div",
        { class: "card" },
        el("p", { class: "form-error" }, `Page module ${moduleUrl} exports no mount() function.`)
      )
    );
    return;
  }

  await fn(root, { pageId });
}
