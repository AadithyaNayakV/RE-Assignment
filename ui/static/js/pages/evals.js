// The golden cases with their last saved baseline result, and a button for the FREE
// evals only. The paid Claude skill evals have no button: their command is shown.

import { add, h, clear, box, errorBox, loading, table, button, code, help, runLog }
  from "../dom.js";
import { getJSON, postJSON, followJob } from "../api.js";

const RESULT_ICON = { pass: "✓", fail: "✗", skip: "–" };

export async function evalsPage(view) {
  add(view, h("h1", { class: "page-title", text: "Evals" }));
  const holder = h("div", { class: "section" }, loading("Reading the test cases…"));
  add(view, holder);
  let data;
  try {
    data = await getJSON("/api/evals");
  } catch (err) {
    add(clear(holder), errorBox("Could not list the evals", err.message));
    return;
  }
  const log = runLog();
  const result = h("div", { "aria-live": "polite" });
  const run = button("Run free evals", async () => {
    run.disabled = true;
    log.details.hidden = false;
    add(clear(result), loading("Running checks: the engine evals, then the template report evals. This takes a few minutes…"));
    try {
      const job = await postJSON("/api/jobs/evals", {});
      const done = await followJob(job, (j) => { log.pre.textContent = j.log.join("\n"); });
      add(clear(result), box(done.status === "succeeded" ? "ok" : "error",
        `Evals ${done.status}`, "The baselines were not changed; the summary is at the end of the run log."));
    } catch (err) {
      add(clear(result), errorBox("Could not start the evals", err.message));
    } finally {
      run.disabled = false;
    }
  }, { class: "primary" });
  const cell = (s) => h("span", { class: `result result-${s || "none"}` },
    s && RESULT_ICON[s] ? h("span", { class: "result-icon", "aria-hidden": "true",
                                      text: RESULT_ICON[s] }) : null,
    s || "not in baseline");
  const level1 = help("Level 1", "checks the figures the engine writes to metrics.json against independently worked-out values. Free.");
  const level2 = help("Level 2", "checks the written report.md with the same rules for the template and for Claude. Free for the template; Claude runs use Pro usage.");
  add(clear(holder),
    h("p", {}, "Each test case is a workbook with known answers. ",
      "Level 1 ", level1.button, " and Level 2 ", level2.button, " results are from the last saved run."),
    level1.text, level2.text,
    h("p", { class: "hint", text: `Last saved runs: engine ${data.baselines.engine || "none"}; template report ${data.baselines.skill_deterministic || "none"}; Claude ${data.baselines.skill || "none"}.` }),
    table("Test cases", ["Case", "Title", "Engine (Level 1)", "Template report (Level 2)", "Claude report (Level 2)"],
      data.cases.map((c) => [c.id, c.title || "", cell(c.engine), cell(c.skill_deterministic),
        cell(c.skill)])),
    h("h2", { text: "Run the free evals" }),
    h("p", {}, "Runs, one after the other: ", ...data.free_commands.map((c) => [code(c), " "])),
    run, log.details, result,
    h("h2", { text: "Paid evals" }),
    box("warn", "No button, on purpose", data.paid_note),
    h("p", {}, "Run it yourself: ", code(data.paid_command)));
}
