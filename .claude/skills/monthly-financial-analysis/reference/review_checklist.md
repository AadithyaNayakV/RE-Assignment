# Reviewer checklist

Every report ends with a `## Reviewer checklist` section of `- [ ]` items. Include the
**always** items, plus each **conditional** item whose condition holds for this report.
Put in the real counts and labels from the brief.

## Always

- [ ] Source file and period are the ones intended (file name, sha256 prefix, month).
- [ ] Reconciliation result read: K of M checks failed. If any failed, stop: figures that
      depend on them must not be used.
- [ ] Each significant movement checked against what you know about the month. Supply the
      causes: the engine and the skill cannot.
- [ ] Each question under "Questions for the reviewer" answered or removed.
- [ ] Direction words checked against the signs. `check-commentary` checks a word that
      sits directly before a change figure; it cannot check one further away, or one
      next to a figure that matches changes of both signs.
- [ ] Every figure attached to the right line. The guard proves a number exists in
      metrics.json, not that it sits in the right sentence.
- [ ] Context the workbook cannot hold added: contracts won or lost, headcount, timing
      differences, accruals, reclassifications.
- [ ] Signed off, or sent back with the specific figures to re-check.

## Conditional

| Condition (from the brief) | Item |
|---|---|
| Any data-quality **error** | Resolve each error; the N blocked metrics stay n/a until then. |
| Any data-quality **warning** | Read each warning (by code) and decide whether it changes a conclusion. |
| TTM **incomplete** | Note that no trailing-twelve-month figures exist for this period. Do not add one by hand. |
| TTM window has large items | Confirm or dismiss each large TTM-window item; until then the affected TTM figures are not a run rate. |
| Possible one-offs this month | Confirm or dismiss each possible one-off; it is flagged on size only. |
| A line crossed zero (sign change) | Confirm whether each sign change is a reversal, a recovery or a misposting. |
| Classification notes present | Confirm the classification note matches how this client's results are normally presented. |
| Seasonality not assessed | Do not describe any movement as seasonal without at least 24 months of data. |
| Balance sheet present | Confirm the Assets = Liabilities + Equity check result and the balance-sheet date. |
| Any figure from QuickBooks included | Reconcile it to the workbook. The guard did not verify it. |
