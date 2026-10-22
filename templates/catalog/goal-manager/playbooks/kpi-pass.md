# KPI pass

The daily routine. One pass over every KPI that is due, then the status pass. Nothing here needs a human unless
a step says so.

1. `hub kpi list` for every KPI. Skip any whose id starts with `auto:` (Tico computes those). Note each one's
   `cadence`, `freshness`, `definition_version`, `slug` and latest reading.
2. A KPI is due when its latest reading's period has ended and the next period has ended too. A KPI with no folder
   yet is `playbooks/new-kpi.md`, not this pass.
3. For each due KPI, with a budget of 3 minutes:
   - Read `kpis/<slug>/definition.md` and compare its version with the KPI's `definition_version`. If Tico is
     newer, the computation is out of date: report it, skip the KPI, and follow `playbooks/new-kpi.md` next.
   - Run the known-values check first. A mismatch is a failure: skip the KPI and report it.
   - Compute the value for the period that just ended. Post it:
     `hub kpi log <kpi-id> <value> "<how it was computed>" --period-start D --period-end D --evidence "<link or note>" --quality measured --definition-version N`.
     Evidence is a link to the query, the dashboard or the file that shows the inputs. Use `--quality partial`
     when the period is not complete and `estimate` when a source was unavailable and you used a stand-in; say
     which in the note.
   - A wrong earlier reading is corrected with `--supersedes <reading id>` and a note saying what changed. Never
     edit history and never post a second reading for the same period without it.
4. Data missing, a source down, or the budget spent: post nothing for that KPI. Never post a zero for missing
   data. Add one line to the report: the KPI, what was missing, and how long it has been.
5. A KPI that fails twice in a row gets one task for the KPI's owner (`hub task create --owner <owner>`), after
   `hub task list` shows there is not one already.
6. When every due KPI is done or reported, follow `playbooks/status-pass.md`, then, above Passive only
   (`level` in `hub goal ceo-bot`), `playbooks/check-ins.md` for what slipped.
7. Final message, plain: readings posted (KPI, value, period), stale or missing, failures with the reason, and
   suggestions on overridden goals. Commit the repository and rewrite `state.md`.
