// Reports (home): choose client, file, scope and period; see the status badges, the
// report, and the Numbers / Warnings / Charts / Source cells / Review tabs. Every figure
// shown is a string the engine formatted; this page only places it.

import { h, add, clear, box, errorBox, loading, empty, table, badge, button, field, code,
         help, sentence, confirmAction, runLog } from "../dom.js";
import { getJSON, postJSON, qs, followJob } from "../api.js";
import { renderMarkdown } from "../markdown.js";
import { chartCard, barChartCard } from "../charts.js";
import { go, remember, refreshQueueCount } from "../app.js";

const TABS = [["numbers", "Numbers"], ["warnings", "Warnings"], ["charts", "Charts"],
              ["trace", "Source cells"], ["review", "Review"]];
const MAX_ROWS = 400;

/** "n/a (prior period is in a different file)": the engine's text and its reason. */
function withReason(d) {
  return d.reason ? `${d.display} (${d.reason})` : d.display;
}

/** A cell's raw content exactly as read: text in quotes, a number as stored, or blank. */
function rawText(value) {
  return value === null || value === undefined ? "(blank cell)" : JSON.stringify(value);
}

/** "not in review (changed since it was reviewed)" -> "Not in review": the short form
 *  for the Review tab. */
function shortReview(label) {
  return sentence(String(label || "").split(" (")[0].split(" — ")[0]);
}

/** The badge style for a review label (the engine's words; nothing is computed). */
function reviewKind(label) {
  const short = String(label || "").split(" (")[0];
  return { reviewed: "ok", "ready for review": "info" }[short] || "warn";
}

function terminalScope(where) {
  const parts = [];
  if (where.scope === "month") parts.push(`--period ${where.period}`);
  if (where.scope === "year") parts.push(`--scope year --year ${where.year}`);
  if (where.scope === "all") parts.push("--scope all");
  if (where.file) parts.push(`--file "${where.file}"`);
  return parts.join(" ");
}

export async function reportsPage(view, params) {
  add(view, h("h1", { class: "page-title", text: "Reports" }));
  const controls = h("form", { class: "controls", "aria-label": "Choose a report",
                               onsubmit: (e) => e.preventDefault() });
  const status = h("div", { class: "status-block", "aria-label": "Status", hidden: true });
  const body = h("div", { class: "report-area" });
  add(view, controls, status, body);
  add(controls, loading("Loading clients…"));

  let clients;
  try {
    clients = await getJSON("/api/clients");
  } catch (err) {
    add(clear(controls), errorBox("Could not list the companies", err.message));
    return;
  }
  clear(controls);
  if (!clients.clients.length) {
    add(controls, empty("No companies yet. Add one to start.",
      h("a", { href: "#/add", class: "button-link", text: "Add a company" })));
    return;
  }
  const client = params.client && clients.clients.some((c) => c.client_id === params.client)
    ? params.client : clients.clients[0].client_id;
  const state = { client, file: params.file || "", scope: params.scope || "month",
                  period: params.period || "", year: params.year || "",
                  tab: params.tab || "numbers", metric: params.metric || "" };

  const clientSelect = h("select", { name: "client",
    onchange: (e) => go("reports", { client: e.target.value }) },
    clients.clients.map((c) => h("option", { value: c.client_id,
      selected: c.client_id === client ? true : null,
      text: `${c.company_name || c.client_id} (${c.client_id})${c.error ? " — config error" : ""}` })));
  add(controls, field("Company", clientSelect));

  // ---- files and periods
  let files;
  try {
    files = await getJSON(`/api/clients/${encodeURIComponent(client)}/files`);
  } catch (err) {
    add(controls, errorBox("Could not list this company's files", err.message));
    return;
  }
  const quickbooks = files.source_type === "quickbooks";
  const onFile = (e) => go("reports", { ...state, file: e.target.value, period: "",
                                         year: "", tab: state.tab });
  const chosen = files.files.find((f) => f.filename === state.file);
  let fileSelect;
  if (quickbooks) {
    // Snapshots as the engine labels them, newest pull first; unreadable ones last and
    // disabled, each with its one-line reason.
    const option = (f) => h("option", { value: f.filename,
      selected: f.filename === state.file ? true : null, text: f.label });
    fileSelect = h("select", { name: "file", onchange: onFile },
      h("option", { value: "", text: "Newest readable snapshot" }),
      files.files.filter((f) => f.readable).map(option),
      files.files.some((f) => !f.readable)
        ? h("optgroup", { label: "Cannot be read" },
          files.files.filter((f) => !f.readable).map((f) => h("option", {
            value: f.filename, disabled: true, text: `${f.label}: ${f.reason}` })))
        : null);
  } else {
    fileSelect = h("select", { name: "file", onchange: onFile },
      h("option", { value: "", text: files.default_file
        ? `Default file (${files.default_file})` : "Default file: choose one below" }),
      files.files.map((f) => h("option", { value: f.filename,
        selected: f.filename === state.file ? true : null,
        text: `${f.filename} (${f.first_period || "?"} to ${f.last_period || "?"})` })));
  }
  add(controls, field(quickbooks ? "Snapshot" : "File", fileSelect,
    !quickbooks && files.files.length > 1
      ? "Files are never merged: each report reads one file." : null));

  const scopeBox = h("fieldset", { class: "scope" }, h("legend", { text: "Scope" }),
    [["month", "Month"], ["year", "Year"], ["all", "Whole file"]].map(([value, label]) =>
      h("label", { class: "radio" },
        h("input", { type: "radio", name: "scope", value,
          checked: state.scope === value ? true : null,
          onchange: () => go("reports", { ...state, scope: value }) }), label)));
  add(controls, scopeBox);

  let periods;
  try {
    periods = await getJSON(`/api/clients/${encodeURIComponent(client)}/periods?${qs({ file: state.file })}`);
  } catch (err) {
    // One short sentence; the engine's full message is in list-periods.
    add(controls, box("warn", chosen && chosen.reason
      ? `This snapshot cannot be read: ${chosen.reason}.`
      : (state.file ? `${state.file} cannot be read with this company's settings.`
        : "Choose a file to see its months.")));
    return;
  }
  if (!state.period && periods.periods.length) state.period = periods.periods[0].period;
  if (!state.year && periods.years.length) state.year = periods.years[0];
  if (state.scope === "month") {
    add(controls, field("Month", h("select", { name: "period",
      onchange: (e) => go("reports", { ...state, period: e.target.value }) },
      periods.periods.map((p) => h("option", { value: p.period,
        selected: p.period === state.period ? true : null,
        text: `${p.period}${p.ttm_complete ? "" : " (12-month total incomplete)"}` })))));
  } else if (state.scope === "year") {
    add(controls, field("Year", h("select", { name: "year",
      onchange: (e) => go("reports", { ...state, year: e.target.value }) },
      periods.years.map((y) => h("option", { value: y,
        selected: String(y) === String(state.year) ? true : null, text: y })))));
  }
  if (quickbooks && !state.file) {
    add(controls, h("p", { class: "hint controls-note", text: files.default_note }));
  }
  remember("reports", state);

  const where = { client, scope: state.scope, file: state.file,
                  period: state.scope === "month" ? state.period : "",
                  year: state.scope === "year" ? state.year : "" };
  add(body, loading("Loading the report…"));
  let report = null;
  let problem = null;
  try {
    report = await getJSON(`/api/report?${qs(where)}`);
  } catch (err) {
    problem = err;
  }
  clear(body);
  if (!report) {
    const next = where.scope === "month" ? "Choose a month and press Generate report."
      : (where.scope === "year" ? "Choose a year and press Generate report."
        : "Press Generate report.");
    add(body, String(problem.message).startsWith("No report yet")
      ? empty(`No report yet. ${next}`,
        h("p", { class: "hint" }, "Terminal: ",
          code(`python -m finance_engine report --client ${client} ${terminalScope(where)}`)))
      : errorBox("Could not open the report", problem.message));
    add(body, await generatePanel(where));
    return;
  }
  add(clear(status), badges(report));
  status.hidden = false;
  add(body, await generatePanel(where, null, { again: true }));
  add(body, renderReport(report, where, state));
}

// ---- generate ------------------------------------------------------------------------

function terminal(where, keep) {
  return `python -m finance_engine report --client ${where.client} ${terminalScope(where)}`
    + ` --provider ${keep ? "deterministic" : "auto"}`;
}

/** The Generate panel. With opts.again (a report already exists) it is folded away and
 *  its button is secondary: the next step is then the review, not a new run. */
export async function generatePanel(where, onDone, opts) {
  const again = Boolean(opts && opts.again);
  const keep = h("input", { type: "checkbox", id: "keep-local", checked: true });
  const notice = h("div", { class: "notice", "aria-live": "polite" });
  const cmd = code(terminal(where, true));
  const log = runLog();
  const result = h("div", { "aria-live": "polite" });
  const go_ = button("Generate report", run, { class: again ? "secondary" : "primary" });

  async function refreshNotice() {
    cmd.textContent = terminal(where, keep.checked);
    add(clear(notice), loading("Checking what would leave this machine…"));
    try {
      const n = await getJSON(`/api/cloud-notice?${qs({ ...where,
        keep_data_local: keep.checked ? "true" : "false" })}`);
      add(clear(notice), box(n.leaves_machine ? "warn" : "info",
        n.leaves_machine ? "Before this run: what leaves this machine" : "Data stays on this machine",
        n.text));
    } catch (err) {
      add(clear(notice), errorBox("Could not check who would write the commentary",
        err.message));
    }
  }
  keep.addEventListener("change", refreshNotice);

  async function run() {
    go_.disabled = true;
    log.details.hidden = false;
    add(clear(result), loading("Writing the report: reading the file, running the checks, then the number check…"));
    try {
      const job = await postJSON("/api/jobs/report",
        { ...where, keep_data_local: keep.checked });
      const done = await followJob(job, (j) => { log.pre.textContent = j.log.join("\n"); });
      const hints = done.log.filter((l) => l.startsWith("hint:") || l.startsWith("error:")
        || l.startsWith("warning:") || l.startsWith("reviewer notes:"));
      clear(result);
      if (done.status === "succeeded") {
        add(result, box("ok", "Report written: it is ready for review", ...hints));
        refreshQueueCount();
        if (onDone) onDone(done);
        else setTimeout(() => go("reports", { ...where }), 600);
      } else {
        add(result, box("error", `The run ${done.status}`, ...hints,
          done.error || "Open the run log for the details."));
      }
    } catch (err) {
      add(clear(result), errorBox("Could not start the run", err.message));
    } finally {
      go_.disabled = false;
    }
  }

  const contents = [
    h("div", { class: "row" },
      h("label", { class: "check-label", for: "keep-local" }, keep,
        "Keep data on this machine (the built-in template writes the commentary)")),
    notice, h("p", { class: "hint" }, "Terminal: ", cmd), go_, log.details, result];
  refreshNotice();
  if (again) {
    return h("details", { class: "section generate" },
      h("summary", { text: "Generate the report again" }), contents);
  }
  return h("section", { class: "section generate", "aria-labelledby": "generate-title" },
    h("h2", { id: "generate-title", text: "Generate a report" }), contents);
}

// ---- status badges -------------------------------------------------------------------

const HELP = {
  checks: ["Checks", "the totals Python adds up are compared with the workbook's own totals; a failed check blocks that figure."],
  number: ["Number check", "every number in the written commentary must match a figure Python calculated; a number that matches none fails it."],
  writer: ["Written by", "who wrote the commentary text: the built-in template, or a cloud writer if one is set up. The numbers always come from Python."],
  left: ["Data left this machine", "yes only when a cloud writer was used: it is sent a compact summary of the figures (and any send-back comment), never the workbook."],
  review: ["Review", "a generated report is Ready for review; a reviewer marks it as reviewed or sends it back (Changes requested)."],
  fingerprint: ["File fingerprint", "a SHA-256 hash of the source file. If the file changes, the fingerprint changes."],
};

function badges(report) {
  const b = report.badges;
  const recon = b.reconciliation;
  const nc = b.number_check;
  const p = b.provider;
  const traced = nc ? (nc.detail.split(" ")[0] || "") : "";
  const tips = Object.fromEntries(Object.entries(HELP).map(([k, [term, text]]) =>
    [k, help(term, text)]));
  const reviewBadge = badge(reviewKind(b.review), "Review", sentence(b.review),
    tips.review.button);
  reviewBadge.querySelector(".badge-value").id = "review-badge-value";
  return [
    h("ul", { class: "badges" },
      badge(recon.all_passed ? "ok" : "bad", "Checks",
        recon.all_passed ? `all pass (${recon.label})` : recon.label, tips.checks.button),
      badge(nc ? (nc.result === "PASS" ? "ok" : "bad") : "warn", "Number check",
        nc ? `${nc.result === "PASS" ? "pass" : nc.result.toLowerCase()}, ${traced} traced`
          : "not run (no report.md)", tips.number.button),
      badge("info", "Written by", p.provider
        ? `${p.provider} (${p.attempt || "first"} attempt)` : "unknown", tips.writer.button),
      badge(p.data_left_machine ? "warn" : "ok", "Data left this machine",
        p.data_left_machine === null ? "unknown" : (p.data_left_machine ? "yes" : "no"),
        tips.left.button),
      reviewBadge),
    h("p", { class: "meta" }, `Scope: ${b.scope_label || "?"}`, " · File fingerprint: ",
      h("span", { class: "mono", text: b.source_sha256
        ? `sha256 ${b.source_sha256.slice(0, 12)}…` : "?" }), " ", tips.fingerprint.button),
    Object.values(tips).map((t) => t.text),
  ];
}

// ---- the report ----------------------------------------------------------------------

function renderReport(report, where, state) {
  const wrap = h("div");
  const exportQs = qs(where);
  if (report.commentary_log) {
    const log = report.commentary_log;
    add(wrap, h("details", { class: "section" },
      h("summary", { text: "How the commentary was written (commentary_log.json)" }),
      table("Writers tried", ["Writer", "Status", "Attempts", "Data sent", "Reason"],
        log.providers.map((pr) => [pr.provider, pr.status, String((pr.attempts || []).length),
          pr.data_sent ? "yes" : "no", pr.reason || ""]), { numeric: [2] }),
      log.reviewer_notes ? h("p", { text: "The last send-back comment was passed to the writer as reviewer notes." }) : null));
  }
  add(wrap, h("div", { class: "row export no-print" },
    h("a", { href: `/api/export/report.md?${exportQs}`, class: "button-link", download: "" },
      "Download report.md"),
    h("a", { href: `/api/export/metrics.json?${exportQs}`, class: "button-link",
             download: "" }, "Download metrics.json"),
    button("Print or save as PDF", () => window.print())));

  const layout = h("div", { class: "report-layout" });
  const article = h("article", { class: "report", "aria-label": "Report" });
  const side = h("aside", { class: "side", "aria-label": "Details" });
  add(layout, article, side);
  add(wrap, layout);

  const tabs = new Tabs(side, state, where);
  if (report.report_md) {
    add(article, renderMarkdown(report.report_md, report.prose_links,
      (metric) => tabs.trace(metric)));
  } else {
    add(article, empty("Only metrics.json exists for these settings. Press Generate report to write report.md."));
  }
  tabs.mount(report);
  return wrap;
}

// ---- side tabs -----------------------------------------------------------------------

class Tabs {
  constructor(host, state, where) {
    this.host = host;
    this.state = state;
    this.where = where;
    this.buttons = {};
    this.panels = {};
  }

  mount(report) {
    this.report = report;
    this.metrics = new Map(report.metrics_doc.metrics.map((m) => [m.name, m]));
    const list = h("div", { class: "tabs", role: "tablist", "aria-label": "Report details" });
    for (const [id, label] of TABS) {
      const btn = h("button", { type: "button", role: "tab", id: `tab-${id}`,
        "aria-controls": `panel-${id}`, onclick: () => this.show(id),
        onkeydown: (e) => this.keys(e, id) }, label);
      this.buttons[id] = btn;
      add(list, btn);
      this.panels[id] = h("div", { role: "tabpanel", id: `panel-${id}`,
                                   "aria-labelledby": `tab-${id}`, tabindex: "0",
                                   hidden: true });
    }
    this.reviewLabel(report.badges.review);
    add(this.host, list, ...Object.values(this.panels));
    if (this.state.metric) this.trace(this.state.metric);
    else this.show(TABS.some(([id]) => id === this.state.tab) ? this.state.tab : "numbers");
  }

  /** The review status on the tab itself ("Review – In review") and in the badge. */
  reviewLabel(label) {
    this.buttons.review.textContent = `Review – ${shortReview(label)}`;
    const value = document.getElementById("review-badge-value");
    if (value) {
      value.textContent = sentence(label);
      const kind = reviewKind(label);
      value.closest(".badge").className = `badge badge-${kind}`;
      value.closest(".badge").querySelector(".badge-icon").textContent =
        { ok: "✓", info: "•" }[kind] || "!";
    }
  }

  keys(e, id) {
    const ids = TABS.map(([t]) => t);
    const at = ids.indexOf(id);
    const next = { ArrowRight: ids[at + 1] || ids[0], ArrowLeft: ids[at - 1] || ids.at(-1),
                   Home: ids[0], End: ids.at(-1) }[e.key];
    if (next) {
      e.preventDefault();
      this.show(next);
      this.buttons[next].focus();
    }
  }

  show(id) {
    for (const [tab] of TABS) {
      const on = tab === id;
      this.buttons[tab].setAttribute("aria-selected", String(on));
      this.buttons[tab].tabIndex = on ? 0 : -1;
      this.panels[tab].hidden = !on;
    }
    this.state.tab = id;
    remember("reports", { ...this.state, metric: id === "trace" ? this.state.metric : "" });
    const panel = this.panels[id];
    if (!panel.dataset.ready) {
      panel.dataset.ready = "1";
      this[`render_${id}`](panel);
    }
  }

  trace(name) {
    this.state.metric = name;
    const panel = this.panels.trace;
    delete panel.dataset.ready;
    clear(panel);
    this.show("trace");
    panel.focus();
  }

  traceButton(name) {
    return this.metrics.has(name)
      ? button("Show", () => this.trace(name),
        { class: "link", "aria-label": `Show the source cells of ${name}` })
      : h("span", { class: "muted", text: "—" });
  }

  // ---- Numbers
  render_numbers(panel) {
    const display = this.report.metric_display;
    const all = this.report.metrics_doc.metrics;
    const out = h("div");
    const filter = h("input", { type: "search" });
    const draw = () => {
      const term = filter.value.trim().toLowerCase();
      const rows = all.filter((m) => !term || m.name.toLowerCase().includes(term)
        || String(m.label || "").toLowerCase().includes(term));
      clear(out);
      if (!rows.length) {
        add(out, empty("No figure matches this filter. Clear the filter to see every figure."));
        return;
      }
      if (rows.length > MAX_ROWS) {
        add(out, h("p", { class: "hint",
          text: `Showing the first ${MAX_ROWS} of ${rows.length} figures; filter to narrow.` }));
      }
      add(out, table("Figures", ["Figure", "Value", "Status", "Source cells"],
        rows.slice(0, MAX_ROWS).map((m) => [
          h("span", {}, h("span", { class: "metric-label", text: m.label || m.name }),
            h("span", { class: "hint", text: m.name })),
          withReason(display[m.name]),
          m.validation_status, this.traceButton(m.name)]), { numeric: [1] }));
    };
    filter.addEventListener("input", draw);
    add(panel, h("p", { class: "hint", text: "Every value was calculated by Python and formatted by the engine." }),
      field("Filter by name or label", filter), out);
    draw();
  }

  // ---- Warnings
  render_warnings(panel) {
    const cells = this.report.cell_warnings;
    const issues = this.report.metrics_doc.data_quality_issues || [];
    const failures = this.report.reconciliation_failures;
    const codes = [...new Set([...issues.map((i) => i.code), ...cells.map((c) => c.code)])];
    const select = h("select", {},
      h("option", { value: "", text: "All codes" }),
      codes.map((c) => h("option", { value: c, text: c })));
    const out = h("div");
    const draw = () => {
      const code_ = select.value;
      clear(out);
      const shown = issues.filter((i) => !code_ || i.code === code_);
      add(out, h("h3", { text: "Data-quality issues" }), shown.length
        ? table("Data-quality issues", ["Severity", "Code", "Message"],
          shown.map((i) => [i.severity, i.code, i.message]))
        : empty("No data-quality issues for this filter."));
      const cellRows = cells.filter((c) => !code_ || c.code === code_);
      add(out, h("h3", { text: "Cell warnings" }), cellRows.length
        ? table("Cell warnings", ["Code", "Sheet", "Cell", "Line", "Month", "Value in the cell", "Source cells"],
          cellRows.map((c) => [c.code, c.sheet, h("span", { class: "mono", text: c.cell }),
            c.label, c.period, rawText(c.raw_value),
            button("Show", () => this.traceCell(c),
              { class: "link", "aria-label": `Show cell ${c.sheet}!${c.cell}` })]))
        : empty("No cell warnings for this filter."));
      if (!code_) {
        add(out, h("h3", { text: "Failed checks" }), failures.length
          ? table("Failed checks", ["Check", "Month", "Python's total", "Workbook total", "Difference", "Source cells"],
            failures.map((f) => [f.key, f.period, f.computed_display,
              f.reported_display, f.difference_display,
              button("Show", () => this.traceFailure(f),
                { class: "link", "aria-label": `Show the rows behind ${f.key} in ${f.period}` })]),
            { numeric: [2, 3, 4] })
          : empty("Every check passed."));
      }
    };
    select.addEventListener("change", draw);
    add(panel, field("Filter by code", select), out);
    draw();
  }

  traceCell(cell) {
    const panel = this.panels.trace;
    this.state.metric = "";
    delete panel.dataset.ready;
    clear(panel);
    this.show("trace");
    add(clear(panel), h("h3", {}, "Cell ", h("span", { class: "mono", text: `${cell.sheet}!${cell.cell}` })),
      table("Cell", ["Field", "Value"], [["Code", cell.code], ["Sheet", cell.sheet],
        ["Cell", h("span", { class: "mono", text: cell.cell })], ["Line", cell.label],
        ["Month", cell.period], ["Value in the cell", rawText(cell.raw_value)],
        ["Message", cell.message]]),
      h("h4", { text: "Figures affected" }),
      cell.affects.length ? h("ul", {}, cell.affects.map((name) => h("li", {},
        name, " ", this.traceButton(name)))) : empty("None listed."));
    panel.focus();
  }

  traceFailure(f) {
    const panel = this.panels.trace;
    this.state.metric = "";
    delete panel.dataset.ready;
    clear(panel);
    this.show("trace");
    add(clear(panel), h("h3", { text: `Failed check: ${f.key} in ${f.period}` }),
      h("p", { text: f.description || "" }),
      table("Failed check", ["Field", "Value"], [["Python's total", f.computed_display],
        ["Workbook total", f.reported_display], ["Difference", f.difference_display],
        ["Note", f.note || ""]]),
      h("h4", { text: "Workbook rows checked" }),
      table("Workbook rows checked", ["Sheet", "Row", "Label", "Formula"],
        (f.evidence || []).map((e) => [e.sheet, String(e.row ?? ""), e.label || "",
          e.formula ? h("span", { class: "mono", text: e.formula }) : "—"])));
    panel.focus();
  }

  // ---- Source cells
  render_trace(panel) {
    const name = this.state.metric;
    if (!name) {
      add(panel, empty("Nothing chosen yet. Click an underlined number in the report, or Show in the Numbers or Warnings tab, to see the cells it comes from."));
      return;
    }
    const m = this.metrics.get(name);
    if (!m) {
      add(panel, box("warn", "Unknown figure", `${name} is not in this metrics.json.`));
      return;
    }
    const d = this.report.metric_display[name];
    const methods = this.report.metrics_doc.methods || {};
    add(panel, h("h3", { text: m.label || name }), h("p", { class: "hint", text: name }),
      table("Figure", ["Field", "Value"], [
        ["Value", withReason(d)],
        ["Reported by the source", d.reported_display || "—"],
        ["Status", m.validation_status],
        ["Method", `${m.method_id}: ${methods[m.method_id] || ""}`],
        ["Notes", (m.notes || []).join(" ")],
        ["Warnings", (m.warnings || []).map((w) => (typeof w === "string" ? w : w.message || w.code)).join(" ") || "—"],
      ]));
    const rows = m.source_rows || [];
    add(panel, h("h4", { text: "Source cells" }), rows.length
      ? table("Source cells", ["Sheet or report", "Cell", "Line", "Month", "Value in the cell", "Other"],
        rows.map((r) => {
          const known = new Set(["sheet", "row", "column", "label", "period", "original_value"]);
          const other = Object.entries(r).filter(([k]) => !known.has(k))
            .map(([k, v]) => `${k}: ${typeof v === "object" ? JSON.stringify(v) : v}`).join("; ");
          return [r.sheet || "", h("span", { class: "mono", text: r.column && r.row
            ? `${r.column}${r.row}` : (r.row ? `row ${r.row}` : "—") }),
          r.label || "", r.period || "", JSON.stringify(r.original_value ?? null), other || "—"];
        }))
      : empty("This figure is worked out from other figures, not read from cells; see its method above."));
  }

  // ---- Charts
  async render_charts(panel) {
    add(panel, loading("Loading the charts…"));
    const scope = this.where.scope;
    const params = { client: this.where.client, file: this.where.file, scope,
                     year: scope === "year" ? this.where.year : "",
                     period: scope === "month" ? this.where.period : "" };
    const which = { month: "this month's report", year: "this year's report",
                    all: "the whole-file report" }[scope];
    const redraw = () => {
      delete panel.dataset.ready;
      clear(panel);
      this.render_charts(panel);
      panel.dataset.ready = "1";
    };
    let data;
    try {
      data = await getJSON(`/api/charts?${qs(params)}`);
    } catch (err) {
      const msg = h("div", { "aria-live": "polite" });
      add(clear(panel), empty(`No charts yet. They are drawn from ${which}: run it to see them.`,
        h("p", { class: "hint", text: err.message }),
        button(`Run ${which} (keeps data on this machine)`, async (e) => {
          e.target.disabled = true;
          add(clear(msg), loading(`Running ${which}…`));
          try {
            const job = await postJSON("/api/jobs/report", { ...this.where,
              keep_data_local: true });
            await followJob(job, () => {});
            redraw();
          } catch (err2) {
            add(clear(msg), errorBox(`Could not run ${which}`, err2.message));
            e.target.disabled = false;
          }
        }), msg));
      return;
    }
    clear(panel);
    if (data.kind === "bar") {
      add(panel, data.charts.map((chart) => barChartCard(chart, data)));
      return;
    }
    const missing = data.net_income_months_not_analysed;
    const netExtra = missing.length ? h("div", { class: "chart-note" },
      h("p", { text: `Not analysed yet, shown as a gap: ${missing.join(", ")}.` }),
      button("Analyse those months", async (e) => {
        e.target.disabled = true;
        const log = runLog();
        const msg = loading("Analysing the missing months, one at a time…");
        log.details.hidden = false;
        e.target.after(msg, log.details);
        try {
          const job = await postJSON("/api/jobs/analyze-months",
            { client: this.where.client, file: this.where.file });
          await followJob(job, (j) => { log.pre.textContent = j.log.join("\n"); });
          redraw();
        } catch (err) {
          msg.replaceWith(errorBox("Could not analyse", err.message));
        }
      }),
      h("p", { class: "hint" }, "Terminal (once per month): ",
        code(`python -m finance_engine analyze --client ${this.where.client} --period YYYY-MM${this.where.file ? ` --file "${this.where.file}"` : ""}`)))
      : null;
    add(panel,
      chartCard("Revenue, gross profit and operating income", data,
        ["revenue.total", "gross_profit", "operating_income"]),
      chartCard("Net income (as reported)", data, ["net_income.reported"], { extra: netExtra }),
      chartCard("Margins", data, ["gross_margin_pct", "operating_margin_pct"]));
  }

  // ---- Review
  async render_review(panel) {
    const q = qs(this.where);
    add(panel, loading("Loading the review status…"));
    let st;
    try {
      st = await getJSON(`/api/review/status?${q}`);
    } catch (err) {
      add(clear(panel), errorBox("Could not read the review log", err.message));
      return;
    }
    clear(panel);
    this.reviewLabel(st.label);
    const reload = () => {
      delete panel.dataset.ready;
      clear(panel);
      this.render_review(panel);
      panel.dataset.ready = "1";
      refreshQueueCount();
    };
    const msg = h("div", { "aria-live": "polite" });
    add(panel, nextStep(st, this.where), h("p", {}, `Status: ${sentence(st.label)}`));
    if (!st.log_intact) add(panel, box("error", "The review log has been edited", "Its hash chain is broken; no new decision can be recorded until it is restored."));

    if (st.state === "ready_for_review") {
      add(panel, this.reviewForm(st, msg, reload), msg);
    } else if (st.state === "changes_requested") {
      add(panel, this.regeneratePanel(st));
    }

    add(panel, h("h4", { text: "Review log (append-only)" }), st.log.length
      ? table("Review log", ["Time (UTC)", "Name", "Action", "Comment", "Written by", "report.md fingerprint"],
        st.log.map((e) => [e.time_utc, e.automatic ? `${e.name} (automatic)` : e.name,
          e.action_label, e.comment || "—", e.provider || "—",
          h("span", { class: "mono", text: `${String(e.report_sha256 || "").slice(0, 12)}…` })]))
      : empty("No review steps yet. Generating the report puts it in review."));
    const viewer = h("div");
    add(panel, h("h4", { text: "Submitted versions" }), st.versions.length
      ? h("ul", {}, st.versions.map((v) => h("li", {}, h("span", { class: "mono", text: v }), " ",
        button("View", async () => {
          add(clear(viewer), loading("Opening the submitted version…"));
          try {
            const doc = await getJSON(`/api/review/version?${qs({ ...this.where, name: v })}`);
            add(clear(viewer), h("details", { open: true, class: "section" },
              h("summary", { text: doc.name }), renderMarkdown(doc.text, [], null)));
          } catch (err) {
            add(clear(viewer), errorBox("Could not open the version", err.message));
          }
        }, { class: "link", "aria-label": `View ${v}` }))))
      : empty("No submitted version yet: a copy is kept each time the report goes into review."),
      viewer);
    add(panel, h("p", { class: "hint" }, "Terminal: ",
      code(`python -m finance_engine review status --client ${this.where.client} ${terminalScope(this.where)}`)));
  }

  /** Ready for review: the reviewer's name and comment, Mark as reviewed or Send back. */
  reviewForm(st, msg, reload) {
    const name = h("input", { type: "text", autocomplete: "name", maxlength: "80",
                              required: true });
    const comment = h("textarea", { rows: "3", maxlength: "4000" });
    const ASK = {
      "mark-reviewed": ["Mark this report as reviewed?", "Your name is recorded in the review log as the reviewer, with the time. The log is append-only: the mark stays until report.md or metrics.json changes.", "Mark as reviewed"],
      "send-back": ["Send this report back?", "Your comment is recorded in the review log and the status becomes Changes requested. The log is append-only.", "Send back"],
    };
    const act = (action) => async () => {
      clear(msg);
      if (!name.value.trim()) {
        add(msg, box("error", "Your name is required", "Type the name to record in the log."));
        name.focus();
        return;
      }
      if (action === "send-back" && !comment.value.trim()) {
        add(msg, box("error", "A comment is required to send it back",
          "Say what should change; it is passed to a cloud writer when the report is regenerated."));
        comment.focus();
        return;
      }
      if (!(await confirmAction(...ASK[action]))) return;
      add(msg, loading("Recording it in the review log…"));
      try {
        await postJSON(`/api/review/${action}`, { ...this.where, by: name.value,
                                                  comment: comment.value });
        reload();
      } catch (err) {
        add(clear(msg), errorBox("Not recorded", err.message));
      }
    };
    return h("div", { class: "review-form" },
      field("Your name", name, st.submitted_by_person && st.require_different_reviewer
        ? `${st.submitted_by_person} submitted this version by hand, so someone else marks it as reviewed (config/review.yaml). Names are self-declared; this is a nudge, not security.`
        : "Names are typed in and self-declared (no sign-in)."),
      field("Comment (required to send back)", comment),
      h("div", { class: "row" },
        button("Mark as reviewed", act("mark-reviewed"), { class: "primary" }),
        button("Send back", act("send-back"))));
  }

  /** Changes requested: regenerate with the comment. A cloud writer receives it as
   *  reviewer notes; the template cannot follow it, and the page says so. */
  regeneratePanel(st) {
    const keepLocal = !st.writer_follows_comments;
    const notice = h("div", { "aria-live": "polite" });
    const result = h("div", { "aria-live": "polite" });
    const log = runLog();
    const run = button("Regenerate with this comment", async () => {
      run.disabled = true;
      log.details.hidden = false;
      add(clear(result), loading("Regenerating the report with the comment…"));
      try {
        const job = await postJSON("/api/jobs/report",
          { ...this.where, keep_data_local: keepLocal });
        const done = await followJob(job, (j) => { log.pre.textContent = j.log.join("\n"); });
        if (done.status === "succeeded") {
          refreshQueueCount();
          go("reports", { ...this.where, tab: "review" });
        } else {
          add(clear(result), box("error", `The run ${done.status}`,
            done.error || "Open the run log for the details."));
        }
      } catch (err) {
        add(clear(result), errorBox("Could not start the run", err.message));
      } finally {
        run.disabled = false;
      }
    }, { class: "primary" });
    if (keepLocal) {
      add(notice, box("warn", "The template writer cannot follow comments",
        "Regenerating refreshes the report and puts it back to Ready for review, but its wording will not change. To act on the comment, edit report.md and submit it from the terminal, or use a cloud writer: open Generate the report again (above) and untick Keep data on this machine."));
    } else {
      add(notice, loading("Checking what would leave this machine…"));
      getJSON(`/api/cloud-notice?${qs({ ...this.where, keep_data_local: "false" })}`)
        .then((n) => add(clear(notice), box(n.leaves_machine ? "warn" : "info",
          n.leaves_machine ? "Before this run: what leaves this machine"
            : "Data stays on this machine", n.text)))
        .catch((err) => add(clear(notice),
          errorBox("Could not check who would write the commentary", err.message)));
    }
    return h("div", { class: "stack" }, notice, run, log.details, result,
      h("p", { class: "hint" }, "Terminal: ",
        code(`python -m finance_engine report --client ${this.where.client} ${terminalScope(this.where)} --provider ${keepLocal ? "deterministic" : "auto"}`)));
  }
}

/** One line at the top of the Review tab: what happens next, from the review state. */
function nextStep(st, where) {
  const last = st.last_entry || {};
  if (st.state === "ready_for_review") {
    return box("info", "Next step: review this report",
      "Read it and its checks, then Mark as reviewed, or Send back with a comment saying what to change.");
  }
  if (st.state === "changes_requested") {
    return box("warn", "Changes requested",
      last.comment ? `Comment from ${last.name}: "${last.comment}"` : null,
      "Next step: regenerate with this comment, or edit report.md by hand and submit it from the terminal.");
  }
  if (st.state === "reviewed") {
    return box("ok", "Reviewed: nothing more to do",
      last.name ? `Marked as reviewed by ${last.name} at ${last.time_utc} (UTC).` : null,
      "Regenerating or editing the report puts it back in review.");
  }
  if (st.entries) {
    return box("warn", "Not in review: the report changed",
      "report.md or metrics.json changed after the last review step (an edit by hand). Whoever edited it submits this version from the terminal, or regenerate the report to put it back in review.",
      h("p", {}, "Terminal: ", code(`python -m finance_engine review submit --client ${where.client} ${terminalScope(where)} --by "Your Name"`)));
  }
  return box("warn", "Not in review",
    "This report was generated before reports went into review automatically. Generate the report again (above) to put it in the review queue.");
}
