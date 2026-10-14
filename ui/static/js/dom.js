// DOM helpers. Every string reaches the page as a text node or an attribute value set
// with setAttribute: nothing is ever parsed as HTML (no innerHTML anywhere in this app),
// so a workbook label such as "<script>" is shown as text.

export function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") el.className = value;
    else if (key === "text") el.textContent = String(value);
    else if (key.startsWith("on") && typeof value === "function") {
      el.addEventListener(key.slice(2), value);
    } else el.setAttribute(key, value === true ? "" : String(value));
  }
  add(el, children);
  return el;
}

export function add(el, ...children) {
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

export function clear(el) {
  el.replaceChildren();
  return el;
}

/** A plain message box: kind is error | warn | info | ok. */
export function box(kind, title, ...lines) {
  return h("div", { class: `box box-${kind}`, role: kind === "error" ? "alert" : "status" },
    title ? h("p", { class: "box-title", text: title }) : null,
    lines.filter(Boolean).map((line) => h("p", { text: line })));
}

export function loading(text) {
  return h("p", { class: "loading", role: "status" },
    h("span", { class: "spinner", "aria-hidden": "true" }), text || "Loading…");
}

export function empty(text, ...extra) {
  return h("div", { class: "empty" }, h("p", { text }), extra);
}

/** A table in a horizontal scroll wrapper, so the page itself never scrolls sideways. */
export function table(caption, headers, rows, opts) {
  const options = opts || {};
  return h("div", { class: "table-wrap", tabindex: "0", role: "region",
                     "aria-label": caption },
    h("table", { class: options.className || "" },
      h("caption", { class: options.showCaption ? "" : "visually-hidden", text: caption }),
      h("thead", {}, h("tr", {}, headers.map((t) => h("th", { scope: "col", text: t })))),
      h("tbody", {}, rows.map((cells) => h("tr", {},
        cells.map((c) => h("td", {}, c instanceof Node ? c : String(c ?? ""))))))));
}

export function badge(kind, label, value) {
  const icon = { ok: "✓", bad: "✗", warn: "!", info: "•" }[kind] || "•";
  return h("li", { class: `badge badge-${kind}` },
    h("span", { class: "badge-icon", "aria-hidden": "true", text: icon }),
    h("span", { class: "badge-label", text: `${label}: ` }),
    h("span", { class: "badge-value", text: value }));
}

export function button(text, onclick, attrs) {
  return h("button", { type: "button", onclick, ...(attrs || {}) }, text);
}

export function field(labelText, control, hint) {
  const id = control.id || `f-${Math.random().toString(36).slice(2)}`;
  control.id = id;
  return h("div", { class: "field" },
    h("label", { for: id, text: labelText }), control,
    hint ? h("p", { class: "hint", text: hint }) : null);
}

export function code(text) {
  return h("code", { class: "cmd", text });
}
