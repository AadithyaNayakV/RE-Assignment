// Everything Ready for review, across clients. A report is put here when it is generated.

import { add, h, clear, errorBox, loading, empty, table, code } from "../dom.js";
import { getJSON, qs } from "../api.js";

export async function queuePage(view) {
  add(view, h("h1", { class: "page-title", text: "Review queue" }),
    h("p", { class: "hint" }, "Reports ready for review, across every company. Each report comes here as soon as it is generated. Terminal: ",
      code("python -m finance_engine review queue")));
  const holder = h("div", { class: "section" }, loading("Reading the review logs…"));
  add(view, holder);
  let data;
  try {
    data = await getJSON("/api/review/queue");
  } catch (err) {
    add(clear(holder), errorBox("Could not read the queue", err.message));
    return;
  }
  clear(holder);
  if (!Array.isArray(data.ready_for_review)) {
    // A `serve` started before an update answers with an older reply shape.
    add(holder, errorBox("Could not read the queue",
      "The server sent a reply this page does not recognise. It is probably running older code: stop `python -m finance_engine serve` and start it again."));
    return;
  }
  if (!data.ready_for_review.length) {
    add(holder, empty("Nothing is ready for review. A report appears here as soon as it is generated.",
      h("a", { href: "#/reports", class: "button-link", text: "Go to Reports" })));
    return;
  }
  add(holder, table("Ready for review", ["Company", "Report", "Submitted by", "At (UTC)", "Written by", "Open"],
    data.ready_for_review.map((q) => [q.client_id, q.folder,
      q.automatic ? `${q.submitted_by} (generated)` : q.submitted_by, q.submitted_at_utc,
      q.provider || "—",
      h("a", { href: `#/reports?${qs({ client: q.client_id, scope: q.scope,
        period: q.period, year: q.year, file: q.file, tab: "review" })}`,
        "aria-label": `Open the review of ${q.client_id} ${q.folder}`, text: "Open review" })])));
}
