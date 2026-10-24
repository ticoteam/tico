# Monthly cap table check

Schedule: the 5th of each month at 09:00 team time (routine `monthly-cap-table-check`), after setup. Budget 30 minutes. The outcome is the report below, ready for a
person to act on. Nothing is sent, paid, signed or changed.

---

## 1. Inputs

The cap table export, grant letters and board consents on the task (ask once if missing), `knowledge/cap-table.md`, and last month's check.

## 2. Work

1. Rebuild the fully diluted table from the sources and compare it with `knowledge/cap-table.md`. Every difference is a line in the report with both figures and both sources.
2. List grants vesting in the next 90 days, and grants with no approval date or no signed document.
3. Check the 409A date against each grant date and against its expiry (twelve months, or sooner after a material event the human tells you about; say it is the lawyer's call).
4. Add each confirmed change to `knowledge/change-log.md` with its approval and document. A change with no source goes on the open list, not the log.
5. If a round is planned, refresh the scenario table: pre- and post-money, the pool top-up, ownership before and after by holder. Label it a draft for discussion.

## 3. Write and hand over

Write `reports/YYYY-MM-DD-cap-table-check.md` in the shape of `knowledge/examples/cap-table-check.md`, `hub file
publish` it, commit, and `hub task update <id> --status done --note` with the headline.
