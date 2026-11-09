# Weekly renewal and health brief

Schedule: Tuesdays at 09:00 team time (routine `weekly-renewal-brief`), after setup. Also run by hand. Budget 40 minutes.

Inputs: `knowledge/renewals.md`, `knowledge/health-rules.md`, `knowledge/health-practice.md`, last week's brief, the account
notes in `knowledge/accounts/`, and the renewal source, usage reading, calls and tickets the task names.

Outcome: one page in the shape of `knowledge/examples/renewal-brief.md`: the headline, every renewal in the next 120 days by
stage with its owner and notice deadline, each account at risk with two reasons, the accounts waiting on a customer with their
dates, a drafted next touch for each yellow or red account, and what could not be read. Nothing is sent and no record changes.

Done when: every renewal in the window has a stage, an owner and a dated source; every yellow or red account has its reasons
and a next touch; the brief is published; and the task note names each notice deadline within 14 days.

---

## 1. Read where things stand

    hub task show <id>

Then `knowledge/renewals.md`, `knowledge/health-rules.md` and last week's brief. Check whether last week's flagged actions
happened, and mark each one done, slipped or dropped.

## 2. Refresh the calendar

Read the renewal source (a CRM read, or the list the owner keeps up to date). List every renewal in the next 120 days: account,
date, notice deadline, owner, value. Sort by date. Mark the playbook stage: 120, 90, 60 or 30 days out. A renewal with no
owner, or no notice deadline, is flagged.

## 3. Read the signals for each account in the window

Check the lifecycle stage first: an account still in onboarding is not scored on renewal health. Apply the four signal families
in `knowledge/health-practice.md`. For each usage number, follow `skills/read-usage-without-misreading/SKILL.md`. For the last
call, use `hub meeting search "<account>"`. Name every signal you could not read.

## 4. Give each account a status

Apply `skills/score-account-health/SKILL.md`: green, yellow, red, or waiting on customer with its date. Give the two facts that
drive the status. Update `knowledge/accounts/<account>.md`. A change since last week reads "moved from yellow to red".

## 5. Draft the next touch for each yellow or red account

One action for one human: who, what and by when. Write the message with `skills/write-a-customer-next-touch/SKILL.md`. Gaps
about price or dates stay marked, as `[price: Account Manager]` or `[date: account owner]`. Never send.

## 6. Write and hand over

Write `reports/YYYY-MM-DD-renewal-brief.md` in the shape of `knowledge/examples/renewal-brief.md`, then
`hub file publish reports/YYYY-MM-DD-renewal-brief.md`. Commit the files, then `hub task update <id> --status done --note "..."`
with the headline, the path, and what you could not read. Always finish the task.
