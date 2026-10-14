// The golden cases with their last saved baseline result, and a button for the FREE
// evals only. The paid Claude skill evals have no button: their command is shown.

import { add, h, clear, box, loading, table, button, code } from "../dom.js";
import { getJSON, postJSON, followJob } from "../api.js";

export async function evalsPage(view) {
  add(view, h("h1", { text: "Evals" }));
  const holder = h("div", { class: "card" }, loading("Reading the golden cases…"));
  add(view, holder);
  let data;
  try {
    data = await getJSON("/api/evals");
  } catch (err) {
    add(clear(holder), box("error", "Could not list the evals", err.message));
    return;
  }
  const log = h("pre", { class: "job-log", hidden: true, "aria-live": "polite" });
  const result = h("div");
  const run = button("Run free evals", async () => {
    run.disabled = true;
    log.hidden = false;
    clear(result);
    try {
      const job = await postJSON("/api/jobs/evals", {});
      const done = await followJob(job, (j) => { log.textContent = j.log.join("\n"); });
      add(result, box(done.status === "succeeded" ? "ok" : "error",
        `Evals ${done.status}`, "The baselines were not changed; the summary is at the end of the log."));
    } catch (err) {
      add(result, box("error", "Could not start the evals", err.message));
    } finally {
      run.disabled = false;
    }
  }, { class: "primary" });
  const cell = (s) => h("span", { class: `result result-${s || "none"}`,
    text: s || "not in baseline" });
  add(clear(holder), 
    h("p", { class: "hint", text: `Last saved baselines: engine ${data.baselines.engine || "none"}; deterministic ${data.baselines.skill_deterministic || "none"}; Claude ${data.baselines.skill || "none"}.` }),
    table("Golden cases", ["Case", "Title", "Engine (Level 1)", "Deterministic (Level 2)", "Claude (Level 2)"],
      data.cases.map((c) => [c.id, c.title || "", cell(c.engine), cell(c.skill_deterministic),
        cell(c.skill)])),
    h("h2", { text: "Run the free evals" }),
    h("p", {}, "Runs, one after the other: ", ...data.free_commands.map((c) => [code(c), " "])),
    run, log, result,
    h("h2", { text: "Paid evals" }),
    box("warn", "No button, on purpose", data.paid_note),
    h("p", {}, "Run it yourself: ", code(data.paid_command)));
}
