# Prepare a board pack, then the minutes

Triggered by a task naming a board or shareholder meeting, or by the calendar 14 days before one. Budget 45 minutes
for the pack, 30 for the minutes. The outcome is a pack the chair can send and, after the meeting, draft minutes
counsel can settle. You never send either and never sign.

---

## 1. The pack

    hub task show <id>

- **Agenda**: approval of the last minutes, matters arising (last meeting's actions and their status), reports
  (the papers the owner names: finance, product, hiring), decisions, any other business.
- **Resolutions**: every decision the meeting must take, in plain resolution wording, including approvals the
  cap table log shows are missing (grants, issuances) and anything investors must consent to under `knowledge/board.md`.
- **Papers**: ask each author on the task for their paper by 7 days before; list what is missing.
- **Notice**: the date the notice must go under the bylaws, and a draft notice for the chair.
Save `reports/packs/YYYY-MM-DD-<entity>-board.md`, mark it **Draft for counsel**, `hub file publish` it and share it with the chair within the requested work. Board messages to outsiders stay drafts until a person turns mail sending on in Tico.

## 2. The minutes

After the meeting, from the imported recording (`hub meeting search "board"`, `hub meeting read <id>`) or
the chair's notes on the task:
- date, time, place or call, who attended and in what capacity, quorum under the bylaws;
- conflicts of interest declared, and who did not vote;
- each resolution as passed or not, with the vote;
- actions with an owner and a date.
Record decisions, not discussion. Never add what was not said. A point you could not hear or find is
`[CONFIRM:...]` for the chair.

## 3. Hand over

Save `reports/minutes/YYYY-MM-DD-<entity>-board.md`, headed **Draft for counsel. Summary for a human, not legal
advice.**, publish it, and put it on the task for the lawyer named at setup. Add it to `knowledge/minute-book.md`
as "draft". It becomes "approved" only when the signed copy arrives on a task.
