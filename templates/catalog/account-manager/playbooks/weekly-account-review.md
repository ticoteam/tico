# Weekly renewal and expansion review

Schedule: Tuesdays at 09:00 team time (routine `weekly-account-review`), after setup. Also run when a task asks.
Budget 40 minutes.

Inputs: `knowledge/renewal-rules.md`, `knowledge/renewals.md`, last week's review, the account plans in
`knowledge/accounts/`, and the CRM, usage and meeting reads the task names.

Outcome: one review in the shape of `knowledge/examples/account-review.md`: the headline, renewals due in the next 120 days
by stage with their notice deadlines, accounts held for health, expansion with evidence, renewal packs ready or waiting on a
price, and what needs a person this week. Nothing is sent or changed.

Done when every renewal in the window has a stage and a dated source, every expansion line has its evidence, the review is
published, and the task note names each notice deadline that falls within 30 days.

---

## 1. Read where things stand

    hub task show <id>

Read `knowledge/renewal-rules.md`, `knowledge/renewals.md` and last week's review. For each action last week proposed, note
whether it was done, slipped or dropped.

## 2. Refresh the calendar

Read each renewal date, notice period and value from the signed contract or a CRM read, following
`skills/read-a-contract-for-renewal-terms/SKILL.md`. Place each renewal in its stage:
- **120 days out:** account plan refreshed, usage against the contract checked.
- **90 days out:** business review held or booked with the customer-success owner; options shaped.
- **60 days out:** renewal pack ready for a person to price.
- **30 days out:** order form out for signature.

A renewal behind its stage is flagged with the step it missed. A notice deadline within 30 days with no decision goes first
in the headline.

## 3. Check health before terms

For each renewal in the next 90 days, read the Customer Success Manager's latest note, or ask:

    hub question ask customer-success "<account> health?" --wait 60

A red account gets no commercial proposal this week. List it under "Held for health" with their note and its date.

## 4. Find expansion

Compare usage with the contract: seats, locations, modules, usage tiers. Read recent calls for goals the customer stated:

    hub meeting search "<account>"
    hub meeting read <id>

Score each candidate with `skills/score-an-expansion-signal/SKILL.md`. Each opportunity is one line: the account, the evidence
with its number and date, and the option to propose. No evidence, no opportunity.

## 5. Prepare the packs due

For each renewal reaching 60 days, follow `playbooks/renewal-pack.md`.

## 6. Write, publish and hand over

Write `reports/YYYY-MM-DD-account-review.md` in the shape of `knowledge/examples/account-review.md`. Update
`knowledge/renewals.md` with the new dates and their sources. Then:

    hub file publish reports/YYYY-MM-DD-account-review.md

Commit, then:

    hub task update <id> --status done --note "..."

The note holds the notice deadlines within 30 days, the packs waiting on a price, and the sources you could not read. Always
finish the task.
