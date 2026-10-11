# Requirements checklist

Derived from `docs/Build Exercise - AI Solutions Builder.pdf` (RE Financial Services,
"Build Exercise — AI Solutions Builder"). Status reflects **Phase 1 only**; the exercise
has four parts and Phase 1 deliberately builds the engine that Parts 2–4 sit on.

Legend: **done** · **partial** · **not started** (planned for a later phase)

---

## Part 1 — Connect the QuickBooks sandbox to Claude

> "Connect your QuickBooks sandbox to Claude, securely and reliably. Your choice of method
> — a native connector, an open-source MCP server you deploy, or a custom MCP built on the
> QuickBooks API. Demonstrate the connection working against the sandbox. Document what you
> chose, what you rejected, and why — including where this would run if a whole team (not
> just your laptop) needed to use it, and how credentials are handled throughout."

| Item | Status | Where |
|---|---|---|
| Intuit developer account + sandbox company | not started | Phase 2 |
| Working connection demonstrated against the sandbox | not started | Phase 2 |
| Decision record: chosen vs rejected methods | not started | Phase 2 |
| Team-hosting answer (not just a laptop) | not started | Phase 4 architecture note |
| Credential handling documented end to end | partial | `.env.example` placeholders only, `.gitignore` covers `.env`/tokens/keys; `CredentialStore` named as an interface in `CLAUDE.md` but not yet implemented |
| `quickbooks-online-mcp-server` clone left untouched | done | Present in the project root, unmodified; no engine code imports it |

**Phase 1 note.** `finance_engine` contains no QuickBooks code by design. The source
boundary is `DataSource` (`finance_engine/sources/base.py`); a QuickBooks source
implements that interface and needs no change to validation, metrics, reconciliation,
commentary or reporting.

---

## Part 2 — A Claude skill producing a monthly trend analysis

> "Build a Claude skill that produces a monthly trend analysis from the provided Excel
> workbook, for a human reviewer to finish: revenue, margins, and operating expenses by
> category, with month-over-month and trailing-twelve-month views, and short written
> commentary flagging the movements a business owner should know about. Structure it so a
> teammate could read, maintain, and extend it — and point it at a different company's data
> without a rewrite."

| Item | Status | Where |
|---|---|---|
| Reads the provided Excel workbook | done | `finance_engine/sources/excel.py`; formulas + cached values, read-only |
| Revenue | done | `revenue.total`, `revenue.stream.*`, shares, MoM, YoY, TTM |
| Margins | done | `gross_margin_pct`, `operating_margin_pct`, point changes, TTM margin |
| Operating expenses **by category** | done | `opex.category.*`, config-assigned, partitioning every line once |
| Operating expenses by subtotal group | done | `opex.group.*`, mirroring the workbook's own presentation |
| Month-over-month view | done | `*.mom_abs`, `*.mom_pct`, `## Month over month` in the report |
| Trailing-twelve-month view | done | `*.ttm`; blocked, never shortened, when the window is incomplete |
| Short written commentary flagging movements | done | `commentary/deterministic.py` + `## Significant movements` |
| Output is **for a human reviewer to finish** | done | Report states it is a draft; `## Reviewer checklist` |
| A teammate could read and maintain it | done | `CLAUDE.md`, `docs/metrics_schema.md`, interfaces at every boundary, 431 tests |
| **Point it at a different company without a rewrite** | done | A new company is one YAML file. Demonstrated by 10 synthetic companies across 3 sheet layouts, zero code changes |
| Packaged as a Claude skill (`SKILL.md`) | not started | Phase 3 |
| Stretch: load the workbook into the sandbox and run end to end | not started | Phase 2/3 |

---

## Part 3 — Eval suite

> "At least five golden cases — a data state paired with the expected analysis output — and
> a concrete description of how you'd grade the system against them and how you'd detect a
> regression after a future change."

| Item | Status | Where |
|---|---|---|
| At least five golden cases | done (10) | `data/synthetic/*.xlsx` + `data/synthetic/expected/*.json` |
| Each case pairs a data state with expected output | done | Expected values computed by `scripts/generate_synthetic.py` **without importing the engine**, so agreement means two implementations concur |
| Grading approach described concretely | partial | Pass/fail assertions in `tests/test_synthetic.py` and reconciliation across every period; a written grading rubric is Phase 3 |
| Regression detection after a future change | done | 431 tests, plus a mutation suite (`pytest -m mutation`) that injects deliberate faults and asserts they are caught |
| Formal eval runner and report | not started | Phase 3 |

The ten cases, each exercising one behaviour:

| Case | Behaviour under test |
|---|---|
| `company_a_normal_growth` | steady growth, stable costs |
| `company_b_revenue_decline` | sustained revenue decline |
| `company_c_expense_spike` | one category jumps and persists |
| `company_d_margin_decline` | COGS outgrowing revenue |
| `company_e_seasonal_business` | seasonal pattern — and that seasonality is still *not asserted* on 18 months |
| `company_f_one_time_expense` | a one-off on a normally zero line (no meaningful multiple) |
| `company_g_missing_data` | two absent months, one blank cell, blocked TTM |
| `company_h_zero_negative_values` | zero and negative bases, negative lines, shifted layout |
| `company_i_multiple_revenue_streams` | six streams, two subtotal groups |
| `company_j_balance_sheet` | income statement plus a balancing balance sheet |

---

## Part 4 — One-page architecture note

> "How you structured the project (files, skills, memory), your hosting choices, and what
> changes when this runs for ten different clients — including how you'd keep each client's
> data separate."

| Item | Status | Where |
|---|---|---|
| Project structure | partial | `CLAUDE.md` has the folder map and boundaries; the one-page note is Phase 4 |
| Skills and memory | not started | Phase 3/4 |
| Hosting choices | not started | Phase 4 |
| What changes at ten clients | partial | Demonstrated in practice (10 synthetic clients already run through one engine); the written analysis is Phase 4 |
| Keeping each client's data separate | done | Everything is keyed by `client_id`: `config/clients/<id>.yaml`, `data/clients/<id>/source/`, `output/clients/<id>/<period>/`. No shared mutable state between clients |

---

## Ground rules

| Rule | Status | Note |
|---|---|---|
| Use any tools, models, documentation, open-source components | done | Built with Claude Code; openpyxl + PyYAML + pytest only |
| Keep everything within the sandbox and the provided workbook | done | No live accounts, no network calls anywhere in `finance_engine` |
| Don't connect live accounts or real data | done | The only real data is the supplied ABC workbook; everything else is clearly labelled SYNTHETIC |
| Be ready to explain every choice | done | Rationale is documented inline where the decision lives, not in a separate essay |

## What "we're looking for" maps to

> "Five things, weighted equally: a connection choice you can defend; careful credential and
> data handling throughout; a skill a teammate could maintain and extend; evals that can
> tell better from worse; and clear, honest communication about what works and what
> doesn't."

| Criterion | Phase 1 position |
|---|---|
| A connection choice you can defend | Not addressed yet (Phase 2). The `DataSource` boundary is in place so the choice is additive, not a rewrite |
| Careful credential and data handling | `.env` git-ignored and never read by the engine; `.env.example` holds placeholders; client data git-ignored; no secret is ever printed; source workbooks opened read-only and verified by SHA-256 |
| A skill a teammate could maintain and extend | One documented contract (`metrics.json`), interfaces at every boundary, a new client is a YAML file, 431 tests including a mutation suite |
| Evals that can tell better from worse | 10 golden cases with independently computed expectations; reconciliation on every period; mutation tests proving the suite fails when the code is wrong |
| Clear, honest communication | Blocked metrics shown as `n/a` with a reason rather than estimated; the guard's false-pass rates measured and published in `docs/metrics_schema.md` rather than claimed to be zero; every report carries its limitations and a reviewer checklist |

---

## Explicitly out of scope in Phase 1

Per the Phase 1 brief: the QuickBooks connection, the Claude skill, the architecture note,
and any deployment. No paid cloud resources were created. No AI provider is configured —
`LLM_PROVIDER=deterministic`, and no API key is assumed to exist.
