// Sources and settings: QuickBooks status and a read-only sandbox pull, and the provider
// chain (names and yes/no only, never a key). The theme switch is in the top bar.

import { add, h, clear, box, errorBox, loading, table, button, field, code, badge,
         confirmAction, runLog } from "../dom.js";
import { getJSON, postJSON, followJob, qs } from "../api.js";

export async function settingsPage(view) {
  add(view, h("h1", { class: "page-title", text: "Sources and settings" }));
  const qb = h("section", { class: "section", "aria-label": "QuickBooks" },
    loading("Checking QuickBooks…"));
  const providers = h("section", { class: "section", "aria-label": "Who writes the commentary" },
    loading("Reading who writes the commentary…"));
  add(view, qb, providers, h("section", { class: "section", "aria-label": "Theme" },
    h("h2", { text: "Theme" }),
    h("p", { text: "Use the Theme switch in the top bar: System, Light or Dark. It is remembered in this browser when storage is available." })));
  await Promise.all([quickbooks(qb), chain(providers)]);
}

async function quickbooks(card) {
  let st;
  try {
    st = await getJSON("/api/quickbooks/status");
  } catch (err) {
    add(clear(card), errorBox("Could not read the QuickBooks status", err.message));
    return;
  }
  const start = h("input", { type: "month", required: true });
  const end = h("input", { type: "month", required: true });
  const client = h("select", {}, st.clients.map((c) => h("option", { value: c, text: c })));
  const confirm = h("input", { type: "checkbox", id: "qb-confirm" });
  const log = runLog();
  const result = h("div", { "aria-live": "polite" });
  const pull = button("Pull from QuickBooks", async () => {
    clear(result);
    if (!start.value || !end.value) {
      add(result, box("error", "Choose the first and last month"));
      return;
    }
    if (!confirm.checked) {
      add(result, box("error", "Confirm the pull first",
        "Tick the box to confirm a read-only sandbox request."));
      confirm.focus();
      return;
    }
    const yes = await confirmAction("Pull from QuickBooks?",
      `This makes one token refresh and one read-only request to the QuickBooks sandbox for ${client.value}, ${start.value} to ${end.value}, and saves the answer as a new snapshot file. Nothing in QuickBooks is changed.`,
      "Pull from QuickBooks");
    if (!yes) return;
    pull.disabled = true;
    log.details.hidden = false;
    add(result, loading("Pulling from QuickBooks…"));
    try {
      const job = await postJSON("/api/jobs/quickbooks-pull",
        { client: client.value, start: start.value, end: end.value, confirm: true });
      const done = await followJob(job, (j) => { log.pre.textContent = j.log.join("\n"); });
      clear(result);
      if (done.status === "succeeded") {
        add(result, box("ok", "Snapshot saved", `${done.result.filename} (${done.result.first_period} to ${done.result.last_period}).`),
          h("a", { class: "button-link", href: `#/reports?${qs({ client: client.value,
            file: done.result.filename, period: done.result.last_period })}`,
            text: "Open it in Reports" }));
      } else {
        add(result, box("error", `The pull ${done.status}`, done.error || "Open the run log for the details."));
      }
    } catch (err) {
      add(clear(result), errorBox("Could not start the pull", err.message));
    } finally {
      pull.disabled = false;
    }
  }, { class: "primary", disabled: st.clients.length ? null : true });
  add(clear(card), h("h2", { text: "QuickBooks" }),
    h("ul", { class: "badges" },
      badge("info", "Environment", "sandbox only, read-only"),
      badge(st.configured ? "ok" : "warn", "Set up", st.configured ? "yes" : "no"),
      badge(st.token_store_present ? "ok" : "info", "Token file",
        st.token_store_present ? "present" : "absent")),
    st.missing_variables.length ? box("warn", "Missing variables (names only; set them in .env)",
      st.missing_variables.join(", ")) : null,
    h("p", { class: "hint", text: "The pull makes one token refresh and one GET of the monthly Profit and Loss from the sandbox, then saves it as a new snapshot file. Nothing in QuickBooks is changed." }),
    st.clients.length ? h("div", { class: "stack" }, field("Company", client),
      h("div", { class: "row" }, field("First month", start), field("Last month", end)),
      h("label", { class: "check-label", for: "qb-confirm" }, confirm,
        "I confirm: read the QuickBooks sandbox (read-only) and save a new snapshot"),
      pull, log.details, result)
      : box("info", "No QuickBooks company yet", "Add a config with source.type quickbooks, then come back here to pull."),
    h("p", { class: "hint" }, "Terminal: ",
      code("python -m finance_engine pull-quickbooks --client qb_sandbox --start 2025-10 --end 2026-09")));
}

async function chain(card) {
  let st;
  try {
    st = await getJSON("/api/providers");
  } catch (err) {
    add(clear(card), errorBox("Could not read who writes the commentary", err.message));
    return;
  }
  add(clear(card), h("h2", { text: "Who writes the commentary (read-only)" }),
    h("p", { text: `Order tried: ${st.order.join(" → ")}. Cloud allowed: ${st.allow_cloud ? "yes" : "no"}. The system picks the writer; the only choice in a run is "Keep data on this machine".` }),
    table("Writers", ["Writer", "Cloud", "Enabled", "Set up", "Missing variables"],
      st.providers.map((p) => [p.provider, p.cloud ? "yes" : "no", p.enabled ? "yes" : "no",
        p.configured ? "yes" : "no", p.missing_variables.join(", ") || "—"])),
    st.what_leaves ? h("p", { class: "hint", text: st.what_leaves }) : null,
    h("p", { class: "hint" }, "Change it in config/providers.yaml. Terminal: ",
      code("python -m finance_engine provider-status")));
}
