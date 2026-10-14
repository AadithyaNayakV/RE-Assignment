// Everything waiting for review, across clients.

import { add, h, clear, box, loading, empty, table, code } from "../dom.js";
import { getJSON, qs } from "../api.js";

export async function queuePage(view) {
  add(view, h("h1", { text: "Review queue" }),
    h("p", { class: "hint" }, "Reports whose status is in_review. Terminal: ",
      code("python -m finance_engine review queue")));
  const holder = h("div", { class: "card" }, loading("Reading the review logs…"));
  add(view, holder);
  let data;
  try {
    data = await getJSON("/api/review/queue");
  } catch (err) {
    add(clear(holder), box("error", "Could not read the queue", err.message));
    return;
  }
  clear(holder);
  if (!data.in_review.length) {
    add(holder, empty("Nothing is waiting for review."));
    return;
  }
  add(holder, table("In review", ["Client", "Report", "Submitted by", "At (UTC)", "Provider", ""],
    data.in_review.map((q) => [q.client_id, q.folder, q.submitted_by, q.submitted_at_utc,
      q.provider || "—",
      h("a", { href: `#/reports?${qs({ client: q.client_id, scope: q.scope,
        period: q.period, year: q.year, file: q.file, tab: "review" })}`, text: "Open" })])));
}
