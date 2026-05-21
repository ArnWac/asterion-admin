// Tiny DOM helpers. No framework, no virtual DOM — just sugar around
// document.createElement so the view modules stay readable.

export function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "html") node.innerHTML = v;
    else if (k === "text") node.textContent = v;
    else if (k.startsWith("on") && typeof v === "function") {
      node.addEventListener(k.slice(2).toLowerCase(), v);
    } else if (k === "dataset" && typeof v === "object") {
      for (const [dk, dv] of Object.entries(v)) {
        if (dv != null) node.dataset[dk] = dv;
      }
    } else if (k in node && typeof v !== "string") {
      node[k] = v;
    } else {
      node.setAttribute(k, v);
    }
  }
  for (const child of [].concat(children)) {
    if (child == null || child === false) continue;
    node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
  }
  return node;
}

export function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

export function mount(root, ...nodes) {
  clear(root);
  for (const n of nodes) {
    if (n != null) root.appendChild(n);
  }
  root.removeAttribute("data-loading");
}

export function setBreadcrumb(parts) {
  const bar = document.getElementById("breadcrumb");
  if (!bar) return;
  clear(bar);
  parts.forEach((part, idx) => {
    if (idx > 0) bar.appendChild(el("span", { class: "bc-sep" }, "›"));
    if (part && part.href) {
      bar.appendChild(el("a", { href: part.href }, part.label));
    } else {
      bar.appendChild(el("span", {}, part && part.label != null ? part.label : String(part)));
    }
  });
}

export function showToast(message, { type = "ok", timeout = 3500 } = {}) {
  const toast = document.getElementById("toast");
  if (!toast) return;
  toast.textContent = message;
  toast.className = "toast" + (type === "error" ? " error" : "");
  toast.hidden = false;
  clearTimeout(showToast._t);
  showToast._t = setTimeout(() => {
    toast.hidden = true;
  }, timeout);
}
