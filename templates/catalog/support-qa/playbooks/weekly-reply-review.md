# Weekly reply review

Schedule: Fridays at 10:00 team time (routine `weekly-reply-review`), after setup. Also run by hand on request. Budget 40 minutes.

Inputs: the sent replies from the last seven days (from the source setup named, or the tasks a human attached), `knowledge/scorecard.md`, `knowledge/calibration.md`, `knowledge/patterns.md`, and the docs the replies should match.

Outcome: one review for the owner: a scored sample, patterns and drafted coaching. Nothing changes in the support tool.

Done when: every sampled reply is scored or listed as unscored with its reason, critical misses are on their own line, the review is published, coaching drafts are on the task for the owner only, and the task is done with the pass rate.

---

## 1. Read where things stand

    hub task show <id>

Then `knowledge/scorecard.md`, `knowledge/calibration.md` and `knowledge/patterns.md`.

## 2. Draw the sample

List the replies sent in the last seven days from the source setup named. Take the agreed
number (default 10, or about 5 percent, whichever is larger): about 70 percent at random, the rest on
purpose (refunds, escalations, reopened tickets, a new hire, anything the owner named). Write down how the
sample was drawn and the total it was drawn from. If fewer than 5 replies exist, review them all and say
no trend can be read.

## 3. Score each reply

Follow `playbooks/review-one-reply.md`. Every reply gets a 1, 2 or 3 on each criterion with one reason and
one quoted sentence, and a note of what was excellent. Personal details stay out of your files.

## 4. Find the patterns

Group the misses: which criterion, how many of the sample, whether it is new or in `knowledge/patterns.md`.
Two or more of the same miss is a pattern; one is an example. Compare with last week's pass rate. A pattern
that a standing answer or policy would fix is a task to the Librarian or the owner.

## 5. Draft the coaching

For each human a note is warranted for, draft two or three sentences: one specific strength with its
quote, one specific change with an example of the better wording. Coaching notes are drafts for the owner,
named by ticket reference in the shared report and by human only in the task.

## 6. Write the review and hand it over

Write `reports/YYYY-MM-DD-reply-review.md` in the shape of `knowledge/examples/reply-review.md`: headline,
how the sample was drawn, the pass rate and trend, patterns, replies that were excellent, drafted
coaching, unscored replies and why, sources. Then:

    hub file publish reports/YYYY-MM-DD-reply-review.md

## 7. Finish

Commit, then `hub task update <id> --status done --note`: replies reviewed, pass rate, the top pattern
and any source you could not read. Always finish it: an open routine task absorbs the next.

## When a source fails

Name it and what is therefore unscored. A review of the four replies you could read is not "the week".
