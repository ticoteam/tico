# Weekly affiliate review

Schedule: Fridays at 09:00 team time (routine `weekly-affiliate-review`), after setup. Also run when a task asks.
Budget 45 minutes.

Inputs: `knowledge/program-terms.md`, `knowledge/fraud-signals.md`, `knowledge/affiliates/`, last week's review, the
program export for the period (referrals, conversions and commissions by affiliate), and any application on a task.

Outcome: one review in the shape of `knowledge/examples/affiliate-review.md`: the headline, the payout check with each
difference named, a drafted payout list, applications waiting with a proposed decision, fraud flags with their evidence,
and what could not be read. Nothing is paid, approved or removed.

Done when every affiliate in the export has a line, every difference has a reason, the payout list is drafted, each
application has a proposed decision, the review is published, and the task note carries the headline.

---

## 1. Read where things stand

    tico task show <id>

Read last week's review and each affiliate's file. For each decision a person made last week, note whether it was carried
out, still open, or dropped.

## 2. Check the export is usable

Open the period's export. Confirm the period, the currency, and that the row count is plausible for the program. If it
is missing, or covers the wrong dates, the review says so and stops the payout check there. Do not estimate a payout.

## 3. Recompute the payouts

Follow `skills/recompute-a-commission/SKILL.md` for every affiliate with a referral in the period. For each one record the
recomputed amount, the export amount, the difference, and the reason for any difference. Hold the amounts inside a reversal
or hold window as held, not unpaid.

## 4. Check the applications

For each application on a task, follow `skills/review-an-affiliate-application/SKILL.md`. Each gets one proposed decision
with its reason. None is decided here.

## 5. Look for fraud

Follow `skills/check-an-affiliate-for-fraud/SKILL.md` for each affiliate whose referrals changed sharply, whose customers
share a detail with the affiliate, or whose declared traffic does not match its referrals. Each flag names its rule and
its evidence, and one innocent explanation. Record each in `knowledge/affiliates/<name>.md`.

## 6. Draft the payout list and the replies

Save the payout list in `knowledge/drafts/payout-YYYY-MM.md`, marked as a draft for a person to pay. Save each application
decision and each affiliate reply in `knowledge/drafts/` too. Nothing is sent.

## 7. Write the review and publish it

Write `reports/YYYY-MM-DD-affiliates.md` in the shape of `knowledge/examples/affiliate-review.md`:
- headline: one sentence with the result, the count and the total owed;
- payout check: each affiliate, recomputed against export, with each difference named;
- applications: each with its proposed decision and its draft path;
- fraud flags: each with its rule, evidence, innocent explanation and open question;
- could not read: each export or page that failed, and what that leaves unknown;
- sources: each export with its name, period and the date read.

Then:

    tico file publish reports/YYYY-MM-DD-affiliates.md

Add the dated checks to each `knowledge/affiliates/<name>.md`, note progress on the task, and commit this repository.

## 8. Close

    tico task done <id> -m "..." --version N

The note holds the headline, the review path, and what you could not read. Finish the task even on a quiet week: an open
routine task gets absorbed into the next run.

## When a source fails

Name it in the review and in the closing note: which export or page failed, and what that leaves unknown. A missing
export is never "no payouts this week".
