# Weekly lead generation review

Schedule: Mondays at 08:30 team time (routine `weekly-lead-generation-review`), after setup. Also run when a task asks.
Budget 45 minutes.

Inputs: `knowledge/qualified.md`, `knowledge/sources.md`, `knowledge/handoff.md`, last week's review, the lead export for the
week (submitted leads with source, stage and Sales' decision), Sales' accept and reject feedback on the task, and any target
list waiting for verification.

Outcome: one review in the shape of `knowledge/examples/lead-review.md`: the headline, qualified leads by source with the
four-week trend, the hand-off reasons, one source plan for the week, the lead capture drafts, the target lists waiting for
verification, and what could not be read. Nothing is sent, published or bought.

Done when every source in the export has a line, each count names its definition, the source plan proposes one source with
its test and decision date, each draft is saved, the review is published, and the task note carries the headline.

---

## 1. Read where things stand

    tico task show <id>

Read last week's review, `knowledge/sources.md` and `knowledge/handoff.md`. For each source plan last week, note whether its
test ran, its decision date, and what it showed.

## 2. Check the export and the feedback

Confirm the export covers the week, and that each lead has a source field and a Sales decision or "awaiting Sales". If the
export is missing or covers the wrong dates, the review says so and stops the count there. A lead with no source is
reported as unknown.

## 3. Count qualified leads by source

Follow `skills/count-qualified-leads-by-source/SKILL.md`. Record for each source: submitted, accepted, rejected, awaiting Sales,
and the four-week accepted count. Mark any source with too few leads to read.

## 4. Read the hand-off

From Sales' feedback, group the rejects by source and by reason, using Sales' own words. Note any reason that appears on more
than half of one source's rejects. Propose a change to the owner if the reason points at targeting; do not change the
definition in `knowledge/qualified.md` yourself.

## 5. Check the target lists and magnets

For each list in `knowledge/lists/` that changed this week, follow `skills/build-a-target-list/SKILL.md` for the rows added or
verified. For each form or lead magnet with a draft waiting, check the promise against the thank-you copy.

## 6. Propose one source plan

Pick one source to test next. Name its expected volume, its cost in time or money, the test (a number of rows or leads to check
against a rule), the decision date, and who approves any spend. Any change that costs money goes to `paid-media` or to the owner,
never to a purchase from this run. Before any list is handed to `sdr-research`, follow
`skills/check-outreach-before-it-goes/SKILL.md`.

## 7. Write the review and publish it

Write `reports/YYYY-MM-DD-lead-generation.md` in the shape of `knowledge/examples/lead-review.md`:
- headline: one sentence with the accepted count, the change from last week and the top source;
- qualified leads by source, with the definition used and the four-week trend;
- hand-off: the reject reasons in Sales' words, and the leads awaiting Sales;
- source plan: one source, its test and its decision date;
- drafts: each form, magnet and thank-you page with its draft path;
- lists: rows added, rows verified, and rows still to verify;
- could not read: each export or feedback item that is missing, and what that leaves unknown.

Then:

    tico file publish reports/YYYY-MM-DD-lead-generation.md

Add the dated figures to `knowledge/sources.md` and `knowledge/handoff.md`, rewrite `state.md`, and commit this repository.

## 8. Close

    tico task done <id> -m "..." --version N

The note holds the headline, the review path, and what you could not read. Finish the task even on a quiet week: an open
routine task gets absorbed into the next run.

## When a source fails

Name it in the review and in the closing note: which export or feedback failed, and what that leaves unknown. A missing export is
never "no leads this week".
