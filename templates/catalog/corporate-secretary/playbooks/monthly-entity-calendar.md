# Monthly entity and board calendar

Schedule: the 3rd of each month at 09:00 team time (routine `monthly-entity-calendar`), after setup. Also run by hand. Budget 25 minutes. The outcome is one page: what the board and the
entities need in the next 90 days, what waits for a signature, and every record gap. A summary for a human, not
legal advice. Nothing is sent, signed or filed.

---

## 1. Meetings

    hub calendar list

For each board or shareholder meeting in the next 90 days: the date, the notice the bylaws require
(`knowledge/board.md`) and the date notice must go by, the pack due date (default 14 days before), and the pack's
status. A meeting that should happen by the rhythm in `knowledge/board.md` but is not on the calendar is listed.

## 2. Drafts and signatures

From `knowledge/minute-book.md`: minutes drafted but not confirmed by its source, consents drafted but not fully signed, with
days waiting and who each waits on. Check the tasks for signed copies that arrived; file them and update the index.

## 3. Entities

For each entity in `knowledge/entities.md`: annual filings and accounts due inside 90 days (and whether
`compliance` has them), officer or director changes since last month (`hub team show` for people who left), and any
fact older than twelve months to re-confirm.

## 4. Cap table

Read the Equity Manager's change log (`equity-manager`) where that bot exists, otherwise the cap table export on the task (read only; you keep no log of it) against `knowledge/minute-book.md`: any issuance, grant, exercise or transfer since
last month with no matching approval in the minute book goes on the list for the next board pack or a consent.

## 5. Write and hand over

`reports/YYYY-MM-DD-entity-board-calendar.md` in the shape of `knowledge/examples/entity-board-calendar.md`,
ending with the not-legal-advice line. `hub file publish` it, commit, and `hub task update <id> --status done
--note` with the nearest deadline first.
