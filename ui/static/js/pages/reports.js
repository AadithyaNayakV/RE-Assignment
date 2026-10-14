// Reports (home): choose client, file, scope and period; see the status badges, the
// report, and the Numbers / Warnings / Charts / Trace / Review tabs. Every figure shown
// is a string the engine formatted; this page only places it.

import { h, add, clear, box, loading, empty, table, badge, button, field, code }
  from "../dom.js";
import { getJSON, postJSON, qs, followJob } from "../api.js";
import { renderMarkdown } from "../markdown.js";
import { chartCard } from "../charts.js";
import { go, remember } from "../app.js";

const TABS = [["numbers", "Numbers"], ["warnings", "Warnings"], ["charts", "Charts"],
              ["trace", "Trace"], ["review", "Review"]];
const MAX_ROWS = 400;

/** "n/a (prior period is in a different file)": the engine's text and its reason. */
function withReason(d) {
  return d.reason ? `${d.display} (${d.reason})` : d.display;
}

/** A cell's raw content exactly as read: text in quotes, a number as stored, or blank. */
function rawText(value) {
  return value === null || value === undefined ? "(blank cell)" : JSON.stringify(value);
}

export async function reportsPage(view, params) {
  add(view, h("h1", { text: "Reports" }));
  const controls = h("form", { class: "controls card", "aria-label": "Choose a report",
                               onsubmit: (e) => e.preventDefault() });
  const body = h("div", { class: "report-area" });
  add(view, controls, body);
  add(controls, loading("Loading clients…"));

  let clients;
  try {
    clients = await getJSON("/api/clients");
  } catch (err) {
    add(clear(controls), box("error", "Could not list clients", err.message));
    return;
  }
  clear(controls);
  if (!clients.clients.length) {
    add(controls, empty("No clients yet.", h("a", { href: "#/add", class: "button-link",
                                                      text: "Add a company" })));
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
  add(controls, field("Client", clientSelect));

  // ---- files and periods
  let files;
  try {
    files = await getJSON(`/api/clients/${encodeURIComponent(client)}/files`);
  } catch (err) {
    add(controls, box("error", "Could not list this client's files", err.message));
    return;
  }
  const fileSelect = h("select", { name: "file",
    onchange: (e) => go("reports", { ...state, file: e.target.value, period: "", year: "",
                                     tab: state.tab }) },
    h("option", { value: "", text: files.default_file
      ? `All files: the default (${files.default_file})` : "All files: choose one below" }),
    files.files.map((f) => h("option", { value: f.filename,
      selected: f.filename === state.file ? true : null,
      text: `${f.filename} (${f.first_period || "?"} to ${f.last_period || "?"})` })));
  add(controls, field("File", fileSelect, files.files.length > 1
    ? "Files are never merged: one file is read per report." : null));

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
    add(controls, box("warn", "Choose a file", err.message));
    return;
  }
  if (!state.period && periods.periods.length) state.period = periods.periods[0].period;
  if (!state.year && periods.years.length) state.year = periods.years[0];
  if (state.scope === "month") {
    add(controls, field("Month", h("select", { name: "period",
      onchange: (e) => go("reports", { ...state, period: e.target.value }) },
      periods.periods.map((p) => h("option", { value: p.period,
        selected: p.period === state.period ? true : null,
        text: `${p.period}${p.ttm_complete ? "" : " (TTM incomplete)"}` })))));
  } else if (state.scope === "year") {
    add(controls, field("Year", h("select", { name: "year",
      onchange: (e) => go("reports", { ...state, year: e.target.value }) },
      periods.years.map((y) => h("option", { value: y,
        selected: String(y) === String(state.year) ? true : null, text: y })))));
  }
  remember("reports", state);

  const where = { client, scope: state.scope, file: state.file,
                  period: state.scope === "month" ? state.period : "",
                  year: state.scope === "year" ? state.year : "" };
  add(body, await generatePanel(where));
  const reportBox = h("div");
  add(body, reportBox);
  add(reportBox, loading("Loading the report…"));
  let report;
  try {
    report = await getJSON(`/api/report?${qs(where)}`);
  } catch (err) {
    add(clear(reportBox), box("info", "No report to show yet", err.message));
    return;
  }
  add(clear(reportBox), renderReport(report, where, state));
}

// ---- generate ------------------------------------------------------------------------

function terminal(where, keep) {
  const parts = ["python -m finance_engine report", `--client ${where.client}`];
  if (where.scope === "month") parts.push(`--period ${where.period}`);
  if (where.scope === "year") parts.push(`--scope year --year ${where.year}`);
  if (where.scope === "all") parts.push("--scope all");
  if (where.file) parts.push(`--file "${where.file}"`);
  parts.push(`--provider ${keep ? "deterministic" : "auto"}`);
  return parts.join(" ");
}

export async function generatePanel(where, onDone) {
  const panel = h("section", { class: "card generate", "aria-label": "Generate the report" });
  const keep = h("input", { type: "checkbox", id: "keep-local", checked: true });
  const notice = h("div", { class: "notice", "aria-live": "polite" });
  const cmd = code(terminal(where, true));
  const log = h("pre", { class: "job-log", hidden: true, "aria-live": "polite" });
  const result = h("div");
  const go_ = button("Generate report", run, { class: "primary" });

  async function refreshNotice() {
    cmd.textContent = terminal(where, keep.checked);
    add(clear(notice), loading("Checking what would leave this machine…"));
    try {
      const n = await getJSON(`/api/cloud-notice?${qs({ ...where,
        keep_data_local: keep.checked ? "true" : "false" })}`);
      add(clear(notice), box(n.leaves_machine ? "warn" : "info",
        n.leaves_machine ? "Before this run: what leaves this machine" : "Data stays here",
        n.text));
    } catch (err) {
      add(clear(notice), box("error", "Could not check the provider chain", err.message));
    }
  }
  keep.addEventListener("change", refreshNotice);

  async function run() {
    go_.disabled = true;
    log.hidden = false;
    clear(result);
    try {
      const job = await postJSON("/api/jobs/report",
        { ...where, keep_data_local: keep.checked });
      const done = await followJob(job, (j) => { log.textContent = j.log.join("\n"); });
      const hints = done.log.filter((l) => l.startsWith("hint:") || l.startsWith("error:")
        || l.startsWith("warning:") || l.startsWith("reviewer notes:"));
      if (done.status === "succeeded") {
        add(result, box("ok", "Report written", ...hints));
        if (onDone) onDone(done);
        else setTimeout(() => go("reports", { ...where }), 600);
      } else {
        add(result, box("error", `The run ${done.status}`, ...hints,
          done.error || "See the log above."));
      }
    } catch (err) {
      add(result, box("error", "Could not start the run", err.message));
    } finally {
      go_.disabled = false;
    }
  }

  add(panel, [
    h("h2", { text: "Generate" }),
    h("div", { class: "row" },
      h("label", { class: "check-label", for: "keep-local" }, keep,
        " Keep data on this machine (the deterministic template writes the commentary)")),
    notice, h("p", { class: "hint" }, "Terminal equivalent: ", cmd), go_, log, result]);
  refreshNotice();
  return panel;
}

// ---- the report ----------------------------------------------------------------------

function badges(report) {
  const b = report.badges;
  const recon = b.reconciliation;
  const nc = b.number_check;
  const p = b.provider;
  const traced = nc ? (nc.detail.split(" ")[0] || "") : "";
  return h("ul", { class: "badges", "aria-label": "Status" },
    badge(recon.all_passed ? "ok" : "bad", "Reconciliation",
      recon.all_passed ? `all pass (${recon.label})` : recon.label),
    badge(nc ? "info" : "warn", "Number check", nc ? `${traced} traced` : "no report.md"),
    badge(nc && nc.result === "PASS" ? "ok" : "bad", "Guard", nc ? nc.result : "not run"),
    badge("info", "Provider", p.provider
      ? `${p.provider} (${p.attempt || "first"} attempt)` : "unknown"),
    badge(p.data_left_machine ? "warn" : "ok", "Data left this machine",
      p.data_left_machine === null ? "unknown" : (p.data_left_machine ? "yes" : "no")),
    badge("info", "Scope", b.scope_label || "?"),
    badge("info", "Source", b.source_sha256 ? `sha256 ${b.source_sha256.slice(0, 12)}…` : "?"),
    badge(b.review === "approved" ? "ok" : (b.review.startsWith("draft (changed")
      ? "warn" : "info"), "Review", b.review));
}

function renderReport(report, where, state) {
  const wrap = h("div");
  const exportQs = qs(where);
  add(wrap, badges(report));
  if (report.commentary_log) {
    const log = report.commentary_log;
    add(wrap, h("details", { class: "card" },
      h("summary", { text: "How the commentary was written (commentary_log.json)" }),
      table("Providers tried", ["Provider", "Status", "Attempts", "Data sent", "Reason"],
        log.providers.map((pr) => [pr.provider, pr.status, String((pr.attempts || []).length),
          pr.data_sent ? "yes" : "no", pr.reason || ""])),
      log.reviewer_notes ? h("p", { text: "The last send-back comment was passed to the writer as reviewer notes." }) : null));
  }
  add(wrap, h("div", { class: "row export no-print" },
    h("a", { href: `/api/export/report.md?${exportQs}`, class: "button-link", download: "" },
      "Download report.md"),
    h("a", { href: `/api/export/metrics.json?${exportQs}`, class: "button-link",
             download: "" }, "Download metrics.json"),
    button("Print / save as PDF", () => window.print(), { class: "secondary" })));

  const layout = h("div", { class: "report-layout" });
  const article = h("article", { class: "report card", "aria-label": "Report" });
  const side = h("aside", { class: "side card", "aria-label": "Details" });
  add(layout, article, side);
  add(wrap, layout);

  const tabs = new Tabs(side, state, where);
  if (report.report_md) {
    add(article, renderMarkdown(report.report_md, report.prose_links,
      (metric) => tabs.trace(metric)));
  } else {
    add(article, box("info", "Only metrics.json exists for these settings",
      "Generate the report to see report.md here."));
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
    add(this.host, list, ...Object.values(this.panels));
    if (this.state.metric) this.trace(this.state.metric);
    else this.show(TABS.some(([id]) => id === this.state.tab) ? this.state.tab : "numbers");
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
      ? button("Trace", () => this.trace(name), { class: "link", "aria-label": `Trace ${name}` })
      : h("span", { class: "muted", text: "—" });
  }

  // ---- Numbers
  render_numbers(panel) {
    const display = this.report.metric_display;
    const all = this.report.metrics_doc.metrics;
    const out = h("div");
    const filter = h("input", { type: "search", placeholder: "Filter by name or label",
                                "aria-label": "Filter metrics" });
    const draw = () => {
      const term = filter.value.trim().toLowerCase();
      const rows = all.filter((m) => !term || m.name.toLowerCase().includes(term)
        || String(m.label || "").toLowerCase().includes(term));
      clear(out);
      if (!rows.length) { add(out, empty("No metric matches.")); return; }
      if (rows.length > MAX_ROWS) {
        add(out, h("p", { class: "hint",
          text: `Showing the first ${MAX_ROWS} of ${rows.length} metrics; filter to narrow.` }));
      }
      add(out, table("Metrics", ["Metric", "Value", "Status", ""],
        rows.slice(0, MAX_ROWS).map((m) => [
          h("span", {}, h("span", { class: "metric-label", text: m.label || m.name }),
            h("code", { class: "metric-name", text: m.name })),
          withReason(display[m.name]),
          m.validation_status, this.traceButton(m.name)])));
    };
    filter.addEventListener("input", draw);
    add(panel, h("p", { class: "hint", text: "Every value was calculated by Python and formatted by the engine." }),
      filter, out);
    draw();
  }

  // ---- Warnings
  render_warnings(panel) {
    const cells = this.report.cell_warnings;
    const issues = this.report.metrics_doc.data_quality_issues || [];
    const failures = this.report.reconciliation_failures;
    const codes = [...new Set([...issues.map((i) => i.code), ...cells.map((c) => c.code)])];
    const select = h("select", { "aria-label": "Filter warnings by code" },
      h("option", { value: "", text: "All codes" }),
      codes.map((c) => h("option", { value: c, text: c })));
    const out = h("div");
    const draw = () => {
      const code_ = select.value;
      clear(out);
      const shown = issues.filter((i) => !code_ || i.code === code_);
      add(out, h("h3", { text: "Data-quality issues" }), shown.length
        ? table("Data-quality issues", ["Severity", "Code", "Message"],
          shown.map((i) => [i.severity, h("code", { text: i.code }), i.message]))
        : empty("None for this filter."));
      const cellRows = cells.filter((c) => !code_ || c.code === code_);
      add(out, h("h3", { text: "Cell-level warnings" }), cellRows.length
        ? table("Cell-level warnings", ["Code", "Sheet", "Cell", "Line", "Month", "Raw value", ""],
          cellRows.map((c) => [h("code", { text: c.code }), c.sheet, c.cell, c.label, c.period,
            rawText(c.raw_value),
            button("Trace", () => this.traceCell(c), { class: "link" })]))
        : empty("No cell-level warnings for this filter."));
      if (!code_) {
        add(out, h("h3", { text: "Reconciliation failures" }), failures.length
          ? table("Reconciliation failures", ["Check", "Month", "Computed", "Workbook", "Difference", ""],
            failures.map((f) => [h("code", { text: f.key }), f.period, f.computed_display,
              f.reported_display, f.difference_display,
              button("Trace", () => this.traceFailure(f), { class: "link" })]))
          : empty("Every reconciliation check passed."));
      }
    };
    select.addEventListener("change", draw);
    add(panel, field("Code", select), out);
    draw();
  }

  traceCell(cell) {
    const panel = this.panels.trace;
    this.state.metric = "";
    delete panel.dataset.ready;
    clear(panel);
    this.show("trace");
    add(clear(panel), h("h3", { text: `Cell ${cell.sheet}!${cell.cell}` }),
      table("Cell", ["Field", "Value"], [["Code", cell.code], ["Sheet", cell.sheet],
        ["Cell", cell.cell], ["Line", cell.label], ["Month", cell.period],
        ["Raw value", rawText(cell.raw_value)], ["Message", cell.message]]),
      h("h4", { text: "Metrics affected" }),
      cell.affects.length ? h("ul", {}, cell.affects.map((name) => h("li", {},
        h("code", { text: name }), " ", this.traceButton(name)))) : empty("None listed."));
    panel.focus();
  }

  traceFailure(f) {
    const panel = this.panels.trace;
    this.state.metric = "";
    delete panel.dataset.ready;
    clear(panel);
    this.show("trace");
    add(clear(panel), h("h3", { text: `Reconciliation: ${f.key} in ${f.period}` }),
      h("p", { text: f.description || "" }),
      table("Failure", ["Field", "Value"], [["Computed by the engine", f.computed_display],
        ["In the workbook", f.reported_display], ["Difference", f.difference_display],
        ["Note", f.note || ""]]),
      h("h4", { text: "Workbook rows checked" }),
      table("Evidence", ["Sheet", "Row", "Label", "Formula"],
        (f.evidence || []).map((e) => [e.sheet, String(e.row ?? ""), e.label || "",
          e.formula || "—"])));
    panel.focus();
  }

  // ---- Trace
  render_trace(panel) {
    const name = this.state.metric;
    if (!name) {
      add(panel, empty("Click a number in the report, or Trace in the Numbers or Warnings tab, to see where it comes from."));
      return;
    }
    const m = this.metrics.get(name);
    if (!m) {
      add(panel, box("warn", "Unknown metric", `${name} is not in this metrics.json.`));
      return;
    }
    const d = this.report.metric_display[name];
    const methods = this.report.metrics_doc.methods || {};
    add(panel, h("h3", { text: m.label || name }), h("code", { text: name }),
      table("Metric", ["Field", "Value"], [
        ["Value", withReason(d)],
        ["Reported by the source", d.reported_display || "—"],
        ["Status", m.validation_status],
        ["Method", `${m.method_id}: ${methods[m.method_id] || ""}`],
        ["Notes", (m.notes || []).join(" ")],
        ["Warnings", (m.warnings || []).map((w) => (typeof w === "string" ? w : w.message || w.code)).join(" ") || "—"],
      ]));
    const rows = m.source_rows || [];
    add(panel, h("h4", { text: "Source cells" }), rows.length
      ? table("Source cells", ["Sheet / report", "Cell", "Line", "Month", "Raw value", "Other"],
        rows.map((r) => {
          const known = new Set(["sheet", "row", "column", "label", "period", "original_value"]);
          const other = Object.entries(r).filter(([k]) => !known.has(k))
            .map(([k, v]) => `${k}: ${typeof v === "object" ? JSON.stringify(v) : v}`).join("; ");
          return [r.sheet || "", r.column && r.row ? `${r.column}${r.row}` : (r.row ? `row ${r.row}` : "—"),
            r.label || "", r.period || "", JSON.stringify(r.original_value ?? null), other || "—"];
        }))
      : empty("This metric is derived from other metrics, not read from cells; see its method."));
  }

  // ---- Charts
  async render_charts(panel) {
    add(panel, loading("Loading the monthly series…"));
    const params = { client: this.where.client, file: this.where.file,
                     year: this.where.scope === "year" ? this.where.year : "" };
    let data;
    try {
      data = await getJSON(`/api/charts?${qs(params)}`);
    } catch (err) {
      add(clear(panel), box("info", "No charts yet", err.message),
        button("Run the whole-file report (keeps data on this machine)", async (e) => {
          e.target.disabled = true;
          const job = await postJSON("/api/jobs/report", { client: this.where.client,
            scope: "all", file: this.where.file, keep_data_local: true });
          await followJob(job, () => {});
          delete panel.dataset.ready;
          clear(panel);
          this.render_charts(panel);
          panel.dataset.ready = "1";
        }, { class: "secondary" }));
      return;
    }
    clear(panel);
    const missing = data.net_income_months_not_analysed;
    const netExtra = missing.length ? h("div", { class: "chart-note" },
      h("p", { text: `Not analysed yet, shown as a gap: ${missing.join(", ")}.` }),
      button("Analyse those months", async (e) => {
        e.target.disabled = true;
        const log = h("pre", { class: "job-log" });
        e.target.after(log);
        try {
          const job = await postJSON("/api/jobs/analyze-months",
            { client: this.where.client, file: this.where.file });
          await followJob(job, (j) => { log.textContent = j.log.join("\n"); });
          delete panel.dataset.ready;
          clear(panel);
          this.render_charts(panel);
          panel.dataset.ready = "1";
        } catch (err) {
          log.after(box("error", "Could not analyse", err.message));
        }
      }, { class: "secondary" }),
      h("p", { class: "hint", text: "Terminal: python -m finance_engine analyze --client "
        + `${this.where.client} --period YYYY-MM${this.where.file ? ` --file "${this.where.file}"` : ""} (once per month)` }))
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
      add(clear(panel), box("error", "Could not read the review log", err.message));
      return;
    }
    clear(panel);
    const reload = () => { delete panel.dataset.ready; clear(panel); this.render_review(panel); panel.dataset.ready = "1"; };
    const msg = h("div", { "aria-live": "polite" });
    const name = h("input", { type: "text", autocomplete: "name", maxlength: "80",
                              required: true });
    const comment = h("textarea", { rows: "3", maxlength: "4000" });
    const act = (action) => async () => {
      clear(msg);
      if (!name.value.trim()) {
        add(msg, box("error", "Your name is required", "Type the name to record in the log."));
        name.focus();
        return;
      }
      try {
        await postJSON(`/api/review/${action}`, { ...this.where, by: name.value,
                                                  comment: comment.value });
        reload();
      } catch (err) {
        add(msg, box("error", "Refused", err.message));
      }
    };
    add(panel, h("p", { class: `status-line state-${st.state}` },
      h("strong", { text: "Status: " }), st.label),
      h("p", { class: "hint", text: "Names are typed in and self-declared (no sign-in). By default the approver must be a different name from the submitter (config/review.yaml); this is a nudge, not security." }));
    if (!st.log_intact) add(panel, box("error", "The review log has been edited", "Its hash chain is broken; no new decision can be recorded until it is restored."));
    const form = h("div", { class: "review-form" }, field("Your name", name));
    if (st.state === "draft" || st.state === "changes_requested") {
      add(form, field("Comment (optional)", comment),
        button("Submit for review", act("submit"), { class: "primary" }));
    } else if (st.state === "in_review") {
      add(form, field("Comment (required to send back)", comment),
        h("div", { class: "row" },
          button("Approve", act("approve"), { class: "primary" }),
          button("Send back", act("send-back"), { class: "secondary" })));
    } else {
      add(form, h("p", { text: "Approved. A change to report.md or metrics.json returns it to draft." }));
    }
    add(panel, form, msg);
    add(panel, h("h4", { text: "Log (append-only)" }), st.log.length
      ? table("Review log", ["Time (UTC)", "Name", "Action", "Comment", "Provider", "report.md"],
        st.log.map((e) => [e.time_utc, e.name, e.action, e.comment || "—", e.provider || "—",
          `${String(e.report_sha256 || "").slice(0, 12)}…`]))
      : empty("No review actions yet."));
    const viewer = h("div");
    add(panel, h("h4", { text: "Submitted versions" }), st.versions.length
      ? h("ul", {}, st.versions.map((v) => h("li", {}, h("code", { text: v }), " ",
        button("View", async () => {
          add(clear(viewer), loading());
          try {
            const doc = await getJSON(`/api/review/version?${qs({ ...this.where, name: v })}`);
            add(clear(viewer), h("details", { open: true, class: "card" },
              h("summary", { text: doc.name }), renderMarkdown(doc.text, [], null)));
          } catch (err) {
            add(clear(viewer), box("error", "Could not open", err.message));
          }
        }, { class: "link" }))))
      : empty("No version stored yet: one is kept at each submit."), viewer);
    add(panel, h("p", { class: "hint" }, "Terminal: ",
      code(`python -m finance_engine review status --client ${this.where.client} ${this.where.scope === "month" ? `--period ${this.where.period}` : this.where.scope === "year" ? `--scope year --year ${this.where.year}` : "--scope all"}${this.where.file ? ` --file "${this.where.file}"` : ""}`)));
  }
}
