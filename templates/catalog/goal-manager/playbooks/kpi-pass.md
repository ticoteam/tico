# KPI pass

The daily routine. One pass over every KPI that is due, then the status pass. Nothing here needs a human unless
a step says so.

Inputs: `hub kpi list`, the `kpis/<slug>/` folder of each due KPI, and the source tools declared in `tools:`.

1. `hub kpi list` for every KPI. Skip any whose id starts with `auto:` (Tico computes those). Note each one's
   `cadence`, `freshness`, `definition_version`, `slug` and latest reading.
2. A KPI is due when its latest reading's period has ended and the next period has ended too. A KPI with no folder
   yet is `playbooks/new-kpi.md`, not this pass. A KPI that is not due needs no source task and no report line.
3. For each due KPI, with a budget of 3 minutes:
   - Read `kpis/<slug>/definition.md` and compare its version with the KPI's `definition_version`. If Tico is
     newer, the computation is out of date: report it, skip the KPI, and follow `playbooks/new-kpi.md` next.
   - Run the known-values check first. A mismatch is a failure: skip the KPI and report it.
   - A value far outside the KPI's recent range, or one its definition cannot explain, is checked against its source before it
     is posted. A surprising number is investigated, not published.
   - Compute the value for the period that just ended. Post it:
     `hub kpi log <kpi-id> <value> "<how it was computed>" --period-start D --period-end D --evidence "<link or note>" --quality measured --definition-version N`.
     Evidence is a link to the query, the dashboard or the file that shows the inputs. Use `--quality partial`
     when the period is not complete and `estimate` when a source was unavailable and you used a stand-in; say
     which in the note.
   - A wrong earlier reading is corrected with `--supersedes <reading id>` and a note saying what changed. Never
     edit history and never post a second reading for the same period without it.
4. Data missing, a source down, or the budget spent: post nothing for that KPI. Never post a zero for missing
   data. Add one line to the report: the KPI, what was missing, and how long it has been.
5. A KPI that fails twice in a row is a question for its owner: one task (`hub task create --owner <owner>`) that says what
   failed and what you need, after `hub task list` shows there is not one already.
6. When every due KPI is done or reported, follow `playbooks/status-pass.md`, then, above Passive only
   (`level` in `hub goal strategy-bot`), `playbooks/check-ins.md` for a goal that turned yellow or red in this run's status pass only; a goal already red before the run is not a new question. At level 3, then
   `playbooks/acts-on-its-own.md` for each goal that is red or stalled, in this run.
7. Final message, plain: readings posted (KPI, value, period), stale or missing, failures with the reason, and
   suggestions on overridden goals. Commit the repository and rewrite `state.md`.

**Done when:** every due KPI is posted, or reported as missing or failed with the reason, and the status pass has run.
