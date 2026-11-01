# Weekly IT page

Schedule: Mondays at 09:30 team time (routine `weekly-it-page`), after setup. Budget 25 minutes. The outcome is one page: what is open, what repeats, who is joining and
leaving, what access waits, and which devices need attention. Nothing is changed.

Inputs: `knowledge/requests.md`, `knowledge/devices.md`, `knowledge/tools.md`, the open tasks, and `hub team show`.

Done when: every section below is written, or says "none"; P1 items come first; the page is published; and the task
note carries the headline.

---

## 1. The queue

From `knowledge/requests.md` and open tasks: each open request with priority (P1 many people stopped
or security; P2 one person stopped; P3 an inconvenience), age and what it waits on. P1 at the top.

## 2. Repeats

Any problem seen three or more times in 30 days. Name the likely cause and one fix that removes it (a
setting, a guide, a replacement). A missing guide becomes a task for the Librarian (`hub task create --owner librarian`).

## 3. Joiners and leavers

From `hub team show` and tasks: everyone starting or leaving in the next 14 days, and anyone who left in the
last 14. For each, the checklist in `knowledge/checklists/`, each line marked as `skills/run-a-joiner-or-leaver-checklist/SKILL.md`
sets out: done with its date, waiting on whom, or not started. A leaver past their last day with any account unconfirmed is P1.

## 4. Access waiting

Every prepared access change not yet used, with the owner and days waiting. Temporary access
past its end date is listed for removal.

## 5. Devices

From `knowledge/devices.md`: unencrypted, unmanaged, warranty ending in 60 days, no holder, or held
by someone who has left.

## 6. Write and hand over

Write `reports/YYYY-MM-DD-it.md` in the shape of `knowledge/examples/it-page.md`, `hub file publish`
it, commit, then `hub task update <id> --status done --note` with the headline.
