> Historical record from 2026-10-11; the suite now has 20 cases. See evals/README.md.

# demo_backup: what each file is, and what it is a backup of

Honest labels. Say which one you are showing.

| File | What it really is | Backs up |
|---|---|---|
| `abc_inc_2026-06_claude_skill_report.md` | The report **Claude Code wrote through the skill** in the 46/46-passing eval run (`evals/runs/skill_2026-10-11T14-50-30Z`). Re-verified: guard PASS 304/304. | Step 2, if Claude is slow or the Pro limit is reached |
| `abc_inc_2026-06_deterministic_report.md` | Written by the **template provider, no AI**. Passes the same number guard (66/66). | Step 2 and step 6b: the no-AI fallback |
| `company_c_expense_spike_2026-01_deterministic_report.md` | Template report on a synthetic company with an expense spike. | Step 5 |
| `eval_results_engine_level1.json` | Level 1 results, 13/13. | Step 9 |
| `eval_results_skill_level2_claude_full_run.json` | Level 2 results from the full Claude run, 13/13 **after re-grading**: the first grading was 12/13 (a checker false positive, fixed; see `evals/evaluation_report.md`). | Step 9 |
| `quickbooks_sandbox_get_company_info_SAVED.md` | **Saved**, not live, QuickBooks output. | Steps 7 and 8 |

These are backups, not the demo. The real demo is the live system. The ABC files contain
figures from the supplied workbook, so this folder's ABC files are git-ignored.
