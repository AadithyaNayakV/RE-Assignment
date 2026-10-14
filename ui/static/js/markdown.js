// Renders report.md as DOM nodes. Only the constructs the engine writes are supported:
// headings, paragraphs, line breaks, block quotes, bullet / numbered / checkbox lists,
// tables, a rule, **bold**, _italic_ and `code`. Text is always a text node: markdown
// is never turned into an HTML string, so "<script>" in a label stays text.
//
// `links` comes from the engine (finance_engine/trace_links.py): a figure is made
// clickable only where the engine found exactly one metric for it, and only when the
// characters at that position are exactly the figure it named. Nothing is guessed here.

import { h, scrollTable, looksNumeric } from "./dom.js";

const HEADING = /^(#{1,6})\s+(.*)$/;
const LIST_ITEM = /^(\s*)(-|\d+\.)\s+(\[[ xX]\]\s+)?(.*)$/;

export function renderMarkdown(md, links, onTrace, doc) {
  const d = doc || document;
  const make = (tag, attrs, ...kids) => h(tag, attrs, ...kids);
  const byLine = new Map();
  for (const link of links || []) {
    if (!byLine.has(link.line)) byLine.set(link.line, []);
    byLine.get(link.line).push(link);
  }
  for (const list of byLine.values()) list.sort((a, b) => a.start - b.start);

  const lines = String(md || "").split("\n");
  const root = make("div", { class: "md" });
  let i = 0;
  const lineNo = (index) => index + 1;
  const ctx = { byLine, onTrace, d };

  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i += 1; continue; }

    const heading = HEADING.exec(line);
    if (heading) {
      const level = heading[1].length;                       // 1..6, by the pattern
      const offset = line.indexOf(heading[2]);
      root.append(make(`h${level}`, {}, inline(heading[2], lineNo(i), offset, ctx)));
      i += 1;
      continue;
    }
    if (/^-{3,}\s*$/.test(line)) { root.append(make("hr")); i += 1; continue; }

    if (line.trimStart().startsWith("|")) {
      const rows = [];
      while (i < lines.length && lines[i].trimStart().startsWith("|")) {
        rows.push(lines[i]);
        i += 1;
      }
      root.append(renderTable(rows, ctx));
      continue;
    }

    if (line.startsWith(">")) {
      const quote = make("blockquote");
      const p = make("p");
      let first = true;
      while (i < lines.length && lines[i].startsWith(">")) {
        const text = lines[i].replace(/^>\s?/, "");
        const offset = lines[i].length - text.length;
        if (!first) p.append(lines[i - 1].endsWith("  ") ? make("br") : " ");
        p.append(...inline(stripBreak(text), lineNo(i), offset, ctx));
        first = false;
        i += 1;
      }
      quote.append(p);
      root.append(quote);
      continue;
    }

    const item = LIST_ITEM.exec(line);
    if (item) {
      const ordered = item[2] !== "-";
      const list = make(ordered ? "ol" : "ul", { class: item[3] ? "checklist" : "" });
      while (i < lines.length) {
        const m = LIST_ITEM.exec(lines[i]);
        if (!m || (m[2] !== "-") !== ordered) break;
        const text = m[4];
        const offset = lines[i].length - text.length;
        const li = make("li");
        if (m[3]) {
          li.append(make("span", { class: "check", "aria-hidden": "true", text: "☐" }),
            make("span", { class: "visually-hidden", text: "To do: " }));
        }
        li.append(...inline(text, lineNo(i), offset, ctx));
        i += 1;
        while (i < lines.length && /^\s{2,}\S/.test(lines[i]) && !LIST_ITEM.test(lines[i])) {
          const cont = lines[i].trim();
          li.append(" ", ...inline(cont, lineNo(i), lines[i].indexOf(cont), ctx));
          i += 1;
        }
        list.append(li);
      }
      root.append(list);
      continue;
    }

    const p = make("p");
    let first = true;
    while (i < lines.length && lines[i].trim() && !startsBlock(lines[i])) {
      if (!first) p.append(lines[i - 1].endsWith("  ") ? make("br") : " ");
      p.append(...inline(stripBreak(lines[i]), lineNo(i), 0, ctx));
      first = false;
      i += 1;
    }
    root.append(p);
  }
  return root;
}

function startsBlock(line) {
  return HEADING.test(line) || LIST_ITEM.test(line) || line.startsWith(">")
    || line.trimStart().startsWith("|") || /^-{3,}\s*$/.test(line);
}

function stripBreak(text) {
  return text.replace(/\s+$/, "");
}

function renderTable(rows, ctx) {
  const cells = (row) => row.trim().replace(/^\|/, "").replace(/\|$/, "").split("|")
    .map((c) => c.trim());
  const header = cells(rows[0]);
  const body = rows.slice(1).filter((r) => !/^\s*\|?\s*:?-{2,}/.test(r)).map(cells);
  // A column whose cells are all figures (or a dash / n/a) is right-aligned.
  const blank = (c) => !c || c === "—" || c === "-" || c === "n/a";
  const numeric = header.map((_, k) => body.some((r) => looksNumeric(r[k]))
    && body.every((r) => blank(r[k]) || looksNumeric(r[k])));
  const cls = (k) => (numeric[k] ? "num" : null);
  return scrollTable(header.join(", "),
    h("table", {},
      h("thead", {}, h("tr", {}, header.map((c, k) => h("th", { scope: "col", class: cls(k) },
        inline(c, 0, 0, ctx))))),
      h("tbody", {}, body.map((r) => h("tr", {}, r.map((c, k) => h("td", { class: cls(k) },
        inline(c, 0, 0, ctx))))))));
}

/** Inline markdown -> nodes. `col` is where `text` starts within its source line, so a
 *  text run can be matched against the engine's link columns. */
function inline(text, line, col, ctx) {
  const out = [];
  let i = 0;
  let buf = "";
  let bufStart = 0;
  const flush = () => {
    if (buf) out.push(...textRun(buf, line, col + bufStart, ctx));
    buf = "";
  };
  while (i < text.length) {
    if (text.startsWith("**", i)) {
      const end = text.indexOf("**", i + 2);
      if (end > i + 2) {
        flush();
        out.push(h("strong", {}, inline(text.slice(i + 2, end), line, col + i + 2, ctx)));
        i = end + 2;
        continue;
      }
    }
    if (text[i] === "`") {
      const end = text.indexOf("`", i + 1);
      if (end > i) {
        flush();
        out.push(h("code", { text: text.slice(i + 1, end) }));
        i = end + 1;
        continue;
      }
    }
    if (text[i] === "_" && opensItalic(text, i)) {
      const end = closesItalic(text, i + 1);
      if (end > 0) {
        flush();
        out.push(h("em", {}, inline(text.slice(i + 1, end), line, col + i + 1, ctx)));
        i = end + 1;
        continue;
      }
    }
    if (!buf) bufStart = i;
    buf += text[i];
    i += 1;
  }
  flush();
  return out;
}

function opensItalic(text, i) {
  const before = i === 0 ? " " : text[i - 1];
  const after = text[i + 1] || " ";
  return /[\s([{"'“]/.test(before) && /\S/.test(after);
}

function closesItalic(text, from) {
  for (let j = from; j < text.length; j += 1) {
    if (text[j] !== "_") continue;
    const after = text[j + 1] || " ";
    if (/\S/.test(text[j - 1]) && /[\s.,;:!?)\]}"'”]/.test(after)) return j;
  }
  return -1;
}

/** Plain text, with the engine's links placed exactly where it said they are. */
function textRun(str, line, startCol, ctx) {
  const links = ctx.byLine.get(line) || [];
  const out = [];
  let pos = 0;
  for (const link of links) {
    const from = link.start - startCol;
    const to = link.end - startCol;
    if (from < pos || to > str.length) continue;
    if (str.slice(from, to) !== link.text) continue;        // not exactly that figure
    if (from > pos) out.push(document.createTextNode(str.slice(pos, from)));
    out.push(h("button", {
      type: "button", class: "figure-link",
      title: `Source cells: ${link.metric}`,
      "aria-label": `${link.text}: show the source cells of ${link.metric}`,
      onclick: () => ctx.onTrace && ctx.onTrace(link.metric),
    }, link.text));
    pos = to;
  }
  if (pos < str.length) out.push(document.createTextNode(str.slice(pos)));
  return out;
}
