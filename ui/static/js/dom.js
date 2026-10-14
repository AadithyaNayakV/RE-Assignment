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

/** "in_review" -> "In review": an engine status word in sentence case, for display. */
export function sentence(text) {
  const s = String(text ?? "").replace(/_/g, " ");
  return s.charAt(0).toUpperCase() + s.slice(1);
}

const ICONS = { error: "✗", bad: "✗", warn: "!", ok: "✓", info: "•" };

/** A plain message box: kind is error | warn | info | ok. The title carries a word and an
 *  icon, so the kind is never shown by colour alone. */
export function box(kind, title, ...lines) {
  return h("div", { class: `box box-${kind}`, role: kind === "error" ? "alert" : "status" },
    title ? h("p", { class: "box-title" },
      h("span", { class: "box-icon", "aria-hidden": "true", text: ICONS[kind] || "•" }),
      title) : null,
    lines.filter(Boolean).map((line) => (line instanceof Node ? line : h("p", { text: line }))));
}

/** The engine's message in a plain box. A "Run: <command>" in it is shown as the hint. */
export function errorBox(title, message, kind) {
  const text = String(message || "");
  const at = text.indexOf(" Run: ");
  if (at < 0) return box(kind || "error", title, text);
  return box(kind || "error", title, text.slice(0, at),
    h("p", {}, "Hint: run ", code(text.slice(at + 6))));
}

export function loading(text) {
  return h("p", { class: "loading", role: "status", text: text || "Loading…" });
}

/** An empty state: what is missing, then what to do next. */
export function empty(text, ...extra) {
  return h("div", { class: "empty" }, h("p", { text }), extra);
}

const NUMBER = /^[(+\-−]?[$€£¥]?[+\-−]?\d[\d,]*(\.\d+)?%?\)?( pts)?$/;

/** True when a cell's text is a formatted figure (for right alignment only). */
export function looksNumeric(text) {
  const t = String(text ?? "").replace(/\*/g, "").trim();
  return NUMBER.test(t);
}

/** A table in a horizontal scroll wrapper, so the page itself never scrolls sideways.
 *  A hint appears above it while it is wider than the space it has. */
export function scrollTable(caption, tableEl) {
  const hint = h("p", { class: "scroll-hint", hidden: true,
                        text: "Scroll sideways to see every column →" });
  const wrap = h("div", { class: "table-wrap", tabindex: "0", role: "region",
                           "aria-label": caption }, tableEl);
  if (typeof ResizeObserver === "function") {
    const check = () => { hint.hidden = !(wrap.scrollWidth > wrap.clientWidth); };
    const watcher = new ResizeObserver(check);
    watcher.observe(wrap);
    watcher.observe(tableEl);
  }
  return h("div", { class: "table-block" }, hint, wrap);
}

/** opts.numeric: indexes of the columns holding figures (right-aligned). */
export function table(caption, headers, rows, opts) {
  const options = opts || {};
  const numeric = new Set(options.numeric || []);
  const cls = (k) => (numeric.has(k) ? "num" : null);
  return scrollTable(caption,
    h("table", { class: options.className || "" },
      h("caption", { class: options.showCaption ? "" : "visually-hidden", text: caption }),
      h("thead", {}, h("tr", {}, headers.map((t, k) => h("th", { scope: "col", class: cls(k),
                                                               text: t })))),
      h("tbody", {}, rows.map((cells) => h("tr", {},
        cells.map((c, k) => h("td", { class: cls(k) },
          c instanceof Node ? c : String(c ?? ""))))))));
}

export function badge(kind, label, value, helpButton) {
  return h("li", { class: `badge badge-${kind}` },
    h("span", { class: "badge-icon", "aria-hidden": "true", text: ICONS[kind] || "•" }),
    h("span", { class: "badge-label", text: `${label}:` }),
    h("span", { class: "badge-value", text: value }), helpButton || null);
}

export function button(text, onclick, attrs) {
  return h("button", { type: "button", onclick, ...(attrs || {}) }, text);
}

export function field(labelText, control, hint) {
  const id = control.id || `f-${Math.random().toString(36).slice(2)}`;
  control.id = id;
  return h("div", { class: "field" },
    h("label", { for: id }, labelText), control,
    hint ? h("p", { class: "hint", text: hint }) : null);
}

export function code(text) {
  return h("code", { class: "cmd", text });
}

let helpCount = 0;

/** A small "?" button and its one-sentence explanation. The caller places `text` where
 *  it reads well; the button toggles it (Enter or Space; Escape closes). */
export function help(term, sentence_) {
  helpCount += 1;
  const id = `help-${helpCount}`;
  const text = h("p", { class: "help-text", id, hidden: true }, `${term}: ${sentence_}`);
  const toggle = (open) => {
    text.hidden = !open;
    btn.setAttribute("aria-expanded", String(open));
  };
  const btn = h("button", { type: "button", class: "help", "aria-expanded": "false",
    "aria-controls": id, "aria-label": `What is "${term}"?`,
    onclick: () => toggle(text.hidden),
    onkeydown: (e) => { if (e.key === "Escape") toggle(false); } }, "?");
  return { button: btn, text };
}

/** Ask before an action that cannot be undone. Resolves true only for the yes button. */
export function confirmAction(title, message, yesLabel) {
  return new Promise((resolve) => {
    const id = `confirm-${Math.random().toString(36).slice(2)}`;
    const dialog = h("dialog", { class: "confirm-dialog", "aria-labelledby": id });
    if (typeof dialog.showModal !== "function") {
      resolve(window.confirm(`${title}\n\n${message}`));
      return;
    }
    const finish = (ok) => {
      dialog.close();
      dialog.remove();
      resolve(ok);
    };
    const no = button("Cancel", () => finish(false));
    add(dialog, h("h2", { id, text: title }), h("p", { text: message }),
      h("div", { class: "row" }, button(yesLabel, () => finish(true), { class: "primary" }),
        no));
    dialog.addEventListener("cancel", (e) => { e.preventDefault(); finish(false); });
    document.body.append(dialog);
    dialog.showModal();
    no.focus();
  });
}

/** A job's live log, folded away under "Show the run log". */
export function runLog() {
  const pre = h("pre", { class: "job-log", "aria-live": "polite" });
  const details = h("details", { class: "run-log", hidden: true },
    h("summary", { text: "Show the run log" }), pre);
  return { details, pre };
}
