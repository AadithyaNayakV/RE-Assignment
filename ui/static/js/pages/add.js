// Add a company in four steps: upload and draft, review and validate the YAML, approve
// (a typed name is required), then generate the first report. Nothing goes live before
// step 3; the terminal equivalent of each step is shown beside it.

import { add, h, clear, box, loading, table, button, field, code } from "../dom.js";
import { getJSON, postJSON, postForm } from "../api.js";
import { generatePanel } from "./reports.js";
import { go } from "../app.js";

const STEPS = ["Upload", "Review the draft", "Approve", "Generate"];

export async function addPage(view, params) {
  add(view, h("h1", { text: "Add a company" }),
    h("p", { class: "hint", text: "The config is drafted from the workbook, checked by the real engine, and goes live only when a person approves it. The original workbook is never modified." }));
  const steps = h("ol", { class: "steps", "aria-label": "Progress" });
  const stage = h("div", { class: "card" });
  add(view, steps, stage);
  const mark = (n) => {
    add(clear(steps), STEPS.map((s, k) => h("li", {
      class: k === n ? "current" : (k < n ? "done" : ""),
      "aria-current": k === n ? "step" : null, text: s })));
  };
  if (params.client) {
    try {
      const draft = await getJSON(`/api/onboarding/${encodeURIComponent(params.client)}/draft`);
      return reviewStep(stage, mark, draft);
    } catch (err) {
      add(stage, box("warn", "No draft to resume", err.message));
    }
  }
  uploadStep(stage, mark);
}

function uploadStep(stage, mark) {
  mark(0);
  const id = h("input", { type: "text", name: "client_id", required: true,
    pattern: "[a-z0-9_]{2,40}", autocomplete: "off", spellcheck: "false" });
  const name = h("input", { type: "text", name: "name", required: true });
  const file = h("input", { type: "file", name: "file", accept: ".xlsx,.xlsm", required: true });
  const force = h("input", { type: "checkbox", name: "force" });
  const msg = h("div", { "aria-live": "polite" });
  const submit = h("button", { type: "submit", class: "primary" }, "Upload and draft");
  const form = h("form", { class: "stack", onsubmit: async (e) => {
    e.preventDefault();
    clear(msg);
    if (!form.reportValidity()) return;
    const data = new FormData();
    data.set("client_id", id.value.trim());
    data.set("name", name.value.trim());
    data.set("force", force.checked ? "true" : "false");
    data.set("file", file.files[0]);
    submit.disabled = true;
    add(msg, loading("Uploading, detecting the layout and validating with the engine…"));
    try {
      const draft = await postForm("/api/onboarding/draft", data);
      reviewStep(stage, mark, draft);
    } catch (err) {
      add(clear(msg), box("error", "The draft was not created", err.message));
    } finally {
      submit.disabled = false;
    }
  } },
    field("Client id", id, "2 to 40 lowercase letters, digits or underscores, e.g. acme_ltd."),
    field("Company name", name),
    field("Excel workbook (.xlsx or .xlsm, at most 25 MB)", file),
    h("label", { class: "check-label" }, force, " Replace an existing draft for this id"),
    submit, msg,
    h("p", { class: "hint" }, "Terminal: ",
      code('python -m finance_engine init-client --draft --file book.xlsx --client-id acme_ltd --name "Acme Ltd"')));
  add(clear(stage), h("h2", { text: "1. Upload the workbook" }), form);
}

function summaryView(s) {
  return h("div", {},
    table("Detected", ["What", "Value"], [
      ["Sheet", s.sheet], ["Header row", String(s.header_row)],
      ["Months", s.periods.count ? `${s.periods.first} to ${s.periods.last} (${s.periods.count})` : "none"],
      ["Sections", s.sections.map((x) => `${x.header_label} … ${x.total_label}`).join("; ")],
      ["Reported lines", Object.values(s.reported_lines).join(", ") || "none"],
      ["Checks", s.checks || "not run"], ["Status", s.status],
    ], { showCaption: true }),
    s.problems.length ? box("error", "Problems", ...s.problems) : null,
    s.warnings.length ? box("warn", "Warnings", ...s.warnings) : null,
    s.needs_decision.length ? box("info", "Needs your decision", ...s.needs_decision) : null,
    h("details", {}, h("summary", { text: "How each value was detected" }),
      table("Detection", ["What", "Value", "Confidence", "How"],
        s.detected.map((d) => [d.what, typeof d.value === "string" ? d.value : JSON.stringify(d.value),
          d.confidence, d.how]))));
}

function reviewStep(stage, mark, draft) {
  mark(1);
  const client = draft.client_id;
  const editor = h("textarea", { class: "yaml", rows: "24", spellcheck: "false",
    "aria-label": "Draft config (YAML)" });
  editor.value = draft.yaml;
  const result = h("div", { "aria-live": "polite" });
  const goToLine = (line) => {
    const lines = editor.value.split("\n");
    let start = 0;
    for (const l of lines.slice(0, line - 1)) start += l.length + 1;
    editor.focus();
    editor.setSelectionRange(start, start + (lines[line - 1] || "").length);
  };
  const validate = button("Validate", async () => {
    add(clear(result), loading("Validating with the real loader, reader and reconciliation…"));
    try {
      const v = await postJSON(`/api/onboarding/${encodeURIComponent(client)}/validate`,
        { yaml: editor.value });
      clear(result);
      if (v.ok) {
        add(result, box("ok", "Valid", `analyze ${v.latest} reconciled ${v.checks}.`,
          ...v.warnings.map((w) => `warning ${w.code}: ${w.message}`)),
          button("Continue to approval", () => approveStep(stage, mark, client, v),
            { class: "primary" }));
      } else {
        add(result, box("error", "Invalid: fix these, then validate again"),
          h("ul", { class: "errors" }, v.errors.map((e) => h("li", {},
            e.line ? button(`Line ${e.line}`, () => goToLine(e.line), { class: "link" }) : null,
            e.context_line && e.context_line !== e.line
              ? button(`(started at line ${e.context_line})`, () => goToLine(e.context_line), { class: "link" }) : null,
            ` ${e.message}`))));
      }
    } catch (err) {
      add(clear(result), box("error", "Could not validate", err.message));
    }
  }, { class: "primary" });
  add(clear(stage), h("h2", { text: `2. Review the draft for ${client}` }),
    summaryView(draft.summary || {}),
    h("p", { class: "hint", text: `Editing changes only the draft (${draft.config_path}). Validate saves your edit to the draft and checks it.` }),
    editor, h("div", { class: "row" }, validate), result,
    h("p", { class: "hint" }, "Terminal: ",
      code(`python -m finance_engine validate-config --client ${client} --draft --from edited.yaml`)));
}

function approveStep(stage, mark, client, validation) {
  mark(2);
  const name = h("input", { type: "text", required: true, autocomplete: "name",
                            maxlength: "80" });
  const msg = h("div", { "aria-live": "polite" });
  const approve = button("Approve config", async () => {
    clear(msg);
    if (!name.value.trim()) {
      add(msg, box("error", "Your name is required", "It is recorded with the approval."));
      name.focus();
      return;
    }
    try {
      const rec = await postJSON(`/api/onboarding/${encodeURIComponent(client)}/approve`,
        { by: name.value });
      generateStep(stage, mark, client, rec);
    } catch (err) {
      add(msg, box("error", "Not approved", err.message));
    }
  }, { class: "primary" });
  add(clear(stage), h("h2", { text: "3. Approve the config" }),
    box("info", "What approval does", `Re-validates the draft (${validation.checks} last time), copies it to config/clients/${client}.yaml, and records your name, the time and the SHA-256 of the YAML in data/clients/${client}/config_approval.json (on this machine only).`),
    field("Your name (self-declared; there is no sign-in)", name), approve, msg,
    h("p", { class: "hint" }, "Terminal: ",
      code(`python -m finance_engine approve-config --client ${client} --by "Your Name"`)));
}

async function generateStep(stage, mark, client, rec) {
  mark(3);
  const scope = h("select", {},
    h("option", { value: "month", text: `Latest month (${rec.validation.latest})` }),
    h("option", { value: "all", text: "Whole file" }));
  const holder = h("div");
  const draw = async () => {
    const where = scope.value === "month"
      ? { client, scope: "month", period: rec.validation.latest, file: "", year: "" }
      : { client, scope: "all", file: "", period: "", year: "" };
    add(clear(holder), await generatePanel(where, () => go("reports",
      { ...where, tab: "review" })));
  };
  scope.addEventListener("change", draw);
  add(clear(stage), h("h2", { text: "4. Generate the first report" }),
    box("ok", "Approved", `${rec.approved_by} approved ${rec.config_path} at ${rec.approved_at_utc}.`),
    field("Scope", scope), holder);
  await draw();
}
