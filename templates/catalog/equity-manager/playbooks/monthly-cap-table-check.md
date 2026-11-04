# Monthly cap table check

Schedule: the 5th of each month at 09:00 team time (routine `monthly-cap-table-check`), after setup. Budget 30 minutes. Nothing is sent, issued, signed or changed.

**Output:** `reports/YYYY-MM-DD-cap-table-check.md`, in the shape of `knowledge/examples/cap-table-check.md`: headline, open items, vesting in the next 90 days, the 409A dates, a round scenario if one is planned, changes since last month, sources.

**Done when:** the fully diluted table is rebuilt and each difference from `knowledge/cap-table.md` is listed with both sources. Every grant made since the last check has been run through the grant check. The 90-day vesting list is written. Confirmed changes are in the log. The report is published and the task is marked done with the headline.

---

## 1. Inputs

- On the task: the cap table export, the grant letters and the board consents. Ask once if any is missing, and mark the parts that need it as open.
- `knowledge/cap-table.md`, `knowledge/grants.md`, `knowledge/pool-and-409a.md`, and last month's check.

## 2. Steps

1. Rebuild the fully diluted table from the sources and compare it with `knowledge/cap-table.md`. Every difference is a line in the report with both figures and both sources.
2. Run every grant made since the last check through `skills/check-a-grant-before-it-is-recorded/SKILL.md`. A grant with no approval or no signed document goes on the open list.
3. Run `skills/check-vesting-and-exercise-windows/SKILL.md` across all grants: the vesting list for the next 90 days, and any leaver's last day to exercise.
4. Check the 409A in force against each grant date and its 12-month expiry. List the expiry date. Say that counsel decides whether a material event has changed it.
5. Add each confirmed change to `knowledge/change-log.md` with its approval and document. A change with no source goes on the open list, not the log.
6. If a round is planned, refresh the scenario with `skills/build-a-round-scenario/SKILL.md`, labelled a draft.
7. If anything on the escalation list in `AGENT.md` applies, tell the Head of Finance and the owner in one message.

## 3. Write and hand over

Write `reports/YYYY-MM-DD-cap-table-check.md`, publish it with `hub file publish`, commit it, and `hub task update <id> --status done --note` with the headline.
