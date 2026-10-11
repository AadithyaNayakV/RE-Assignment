# Report template

Write `output/clients/<client_id>/<period>/report.md` with these sections, **in this
order**, using these exact `##` headings. A section with nothing to say still appears,
with one line saying so ("None this month.", "Not in this source."). Never drop a heading
and never fill one with something weaker than a figure from the brief.

The eval suite checks the headings, the order of the data-quality section, and the
wording rules below. Keep them.

---

```markdown
# <Company name> — monthly financial analysis, <Mon YYYY>

**Client:** `<client_id>` · **Period:** `<YYYY-MM>` · **Compared with:** `<prior period>`
**Source:** `<workbook path>` (sha256 `<first 16 chars>…`)
**Written by:** Claude Code, `monthly-financial-analysis` skill, from metrics.json.
Every figure was produced by `finance_engine` and checked by `check-commentary`.

> **DRAFT for human review.** Not a reviewed financial statement. It describes what
> changed; the workbook does not record why.

## Data quality and limitations
<!-- FIRST. Every data-quality issue with its `code` and a one-line plain-English
     meaning. Then: reconciliation result, blocked metrics, TTM complete or not,
     seasonality assessed or not, the classification notes, the limitations. -->

## Summary
<!-- 3–6 bullets a business owner should read first. Each one is a figure and why it
     matters, e.g. "Revenue fell -$12,452.80 (-6.0%) to $195,898.16; Usage Fees, 85.1%
     of revenue, accounts for -$11,142.74 of that." Both figures are printed in the
     brief. Nothing is computed. -->


## Revenue
<!-- Total, MoM, YoY if present, each stream with its share and change. -->

## Margins
<!-- Gross profit, gross margin and its change in points, operating income, operating
     margin, net income as reported. Repeat the classification note if there is one. -->

## Operating expenses by category
<!-- Table: category, value, share, MoM, MoM %, TTM. Then the categories that moved
     most. Mention the subtotal groups if the brief lists them. -->

## Month over month
<!-- Table of the headline measures against the prior period, with n/a reasons. -->

## Trailing twelve months
<!-- If complete: the TTM table, plus any warning the brief attaches to TTM figures.
     If incomplete: say "Not available", give the window and stop. No TTM figures. -->

## Significant movements
<!-- Every line the brief lists as significant, largest first: previous, current,
     change, % change or n/a reason, direction. Then 1–2 sentences on why the largest
     ones matter to the business. -->

## Possible one-offs
<!-- The reporting month's possible one-offs AND the large items inside the TTM window.
     Each one is "possible", flagged on size only. Say if none. -->

## Balance sheet
<!-- If present: cash, receivables, deferred revenue, assets, liabilities, equity, and
     the Assets = Liabilities + Equity check result. Otherwise "Not in this source." -->

## Questions for the reviewer
<!-- The ONLY place for possible explanations. Each one is a question, marked
     "possible" / "needs confirmation". Label EVERY figure as "change" or "level", so a
     reviewer never mistakes a movement for a balance. E.g. "Legal Expenses: change
     $34,000.00 (+1,700.0%); level $36,000.00. Is this possibly a new matter, a timing
     difference, or a misposting? Needs confirmation." -->

## Reviewer checklist
<!-- From reference/review_checklist.md, as "- [ ]" items, adapted to this report. -->
```

---

## Wording rules

**Figures**
- Copy every figure exactly as the brief prints it: `$195,898.16`, `-6.0%`, `+3.1 pts`.
  Do not round further (`$196k`), do not convert units, and do not compute a figure that
  isn't printed, such as a difference, sum, average, ratio or growth rate.
- Margin changes are in **points** (`+3.1 pts`), never a percent of a percent.
- Where the brief says `n/a (reason)`, write `n/a` and the reason. Never write 0 for a
  missing value, and never write an infinite or huge percentage.
- **Direction in the word, amount unsigned:** "fell $12,452.80 (-6.0%)", "rose
  $8,763.85 (+21.6%)". Never "fell -$12,452.80". The percentage in brackets keeps its
  sign as the brief prints it.
- Direction words must match the sign. fell, decreased, narrowed, down and lower need a
  negative change; rose, increased, widened, up and higher need a positive one.
  `check-commentary` rejects a mismatch. For a **loss** the words invert: "the operating
  loss narrowed by $8,343.52" means operating income rose $8,343.52. "fell to $X" names
  a level, not a change.
- A line marked `sign change` crossed zero. Describe it by direction only: "swung from
  a $12,935.29 charge to a $1,806.24 credit". Never call it a reversal, a recovery or a
  saving; what it is needs confirmation.
- When two related measures move in opposite directions, give one plain sentence saying
  why both are true. Only if the reason is arithmetic, for example "operating margin
  worsened because revenue fell faster than the loss narrowed". Never if it is a business
  cause.
- Use labels exactly as the brief prints them. Never invent a category, line, client or
  month.

**Causes**
- The workbook has no information about causes. Do not write "due to", "driven by",
  "because of", "as a result of" or "caused by" about a business event. You may say
  *why a movement matters* (its size, its share of costs, its effect on margin).
- A possible explanation goes only under **Questions for the reviewer**. It is phrased
  as a question and marked "possible" or "needs confirmation".
- Never say what a figure *is*: "a reversal", "a recovery", "a refund", "a one-off",
  "a misposting", "a timing difference". Say it "may be" one, and that it needs
  confirmation. The eval check `cause_label_hedged` fails a report that does not.

**Trends, one-offs, seasonality**
- A single-month spike is a **possible one-off**, not a trend. Call something a trend
  only when the brief shows it across the months you name.
- If the brief says seasonality was **not assessed**, say so. Do not call any movement
  seasonal. You may write "could be seasonal; this cannot be confirmed with N months of
  data, needs confirmation".
- If the TTM window is **incomplete**, write "Trailing twelve months: not available
  (window incomplete)". Do not write a TTM, annualised or run-rate figure. Do not sum or
  average the months that exist.

**Scope**
- One client only. Never mention another client's name, id or figures.
- No forecasts and no advice beyond "the reviewer should confirm X".
- Never include credentials, tokens, `.env` values or API keys.
