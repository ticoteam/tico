# Weekly partner pipeline review

Schedule: Thursdays at 09:00 team time (routine `weekly-partner-review`), after setup. Also run by hand. Budget 40 minutes. The outcome is one page: registrations waiting
with a proposed decision, the partner pipeline, and fees due. Nothing is decided, sent or paid.

---

## 1. Read where things stand

    tico task show <id>

Then `knowledge/rules-of-engagement.md`, `knowledge/registrations.md` and last week's review. Check each
decision a human made since: record it with who and when.

## 2. Registrations

Every registration received and not decided: follow `playbooks/check-a-registration.md` for each. Order by
age; one waiting more than two business days goes first.

## 3. Partner-sourced pipeline

From a CRM read or the Account Executive's review: each registered deal's stage, amount, owner, days quiet,
and protection expiry. Flag deals quiet past 14 days and protections expiring in 14 days. For each flagged
deal, prepare one partner update or question, ready to use.

## 4. Partner health

Per partner: deals registered and won in the last 90 days, revenue sourced, last business review. A
partner with nothing in 90 days gets a proposed next step (a joint account list, a review, or letting the
agreement lapse), a human's call.

## 5. Fees due (first review of the month)

For each partner deal paid by the customer last month: the amount collected, the agreement clause and rate,
the fee. Put the list on the task and make requested payments with the necessary Tools; otherwise name the missing access.

## 6. Write and hand over

Write `reports/YYYY-MM-DD-partner-review.md` in the shape of `knowledge/examples/partner-review.md`, `tico file publish` it, commit, and `tico task done <id> -m "..." --version N`: registrations waiting, conflicts,
fees for review, sources not read. Always finish the task.
