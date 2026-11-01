# Late adjustment

Triggered by a task about a number that changed after the period was closed. Budget 15 minutes. Nothing is applied: the outcome is what changed, why, what it touches, and the lines the accountant would enter.

**Inputs:** the task, the closed period's review in `reports/`, the entry or document the task names, and `knowledge/close-history.md`.

**Done when:** every report already sent to a person that carries the old number is listed with its effect; the cause is in `knowledge/close-history.md`; a checklist step exists that would catch it next month; and the task is marked done, waiting on the accountant.

---

1. Read the task and the closed period's review. Name the entry or document that changed the number, with its date and who posted it.
2. Show the effect on the period's reports. List each report already sent to the owner, the board or an investor that carries the old number.
3. Propose the correction as lines for the accountant: account, debit or credit, amount, reason. Apply nothing.
4. Add the cause to `knowledge/close-history.md`: what changed, when it was found, and what it touched.
5. Add or change one step in `knowledge/close-checklist.md` so the cause is caught next month. Say what the step tests.
6. Finish with `hub task update <id> --status done --note`: the effect, the proposed lines, and "waiting for the accountant".
