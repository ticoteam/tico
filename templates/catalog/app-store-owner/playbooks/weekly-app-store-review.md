# Weekly app store review

Schedule: Thursdays at 09:00 team time (routine `weekly-app-store-review`), after setup. Also run when a task asks.
Budget 45 minutes.

Inputs: `knowledge/listings.md`, `knowledge/keywords.md`, `knowledge/reviews.md`, `knowledge/store-rules.md`, last week's
review, and the console exports and review list attached to the task.

Outcome: one review in `reports/YYYY-MM-DD-app-store.md`: the headline, the conversion and ratings per store against the
28 days before, the complaint patterns across the week's reviews, three listing changes with drafts, a draft reply for
each review that needs one, the bugs sent to the product owner, and what could not be read. Nothing in any store changes.

Done when each store has a result or a "not read" line, each change and reply has its draft saved, the review is
published, and the task note carries the headline.

---

## 1. Read where things stand

    tico task show <id>

Read last week's review, `knowledge/changes.md` and `knowledge/reviews.md`. For each change a person published, note its
date, and read the conversion for the 28 days after it. For each reply drafted last week, note whether a person posted it.

## 2. Read conversion and ratings

Follow `skills/read-a-rating-trend/SKILL.md` for each store's rating, and read impression to install from the export per
store and per listing. Name the rating and the count each number uses. If an export is missing, the review says so for that
store and leaves its numbers blank.

## 3. Triage the week's reviews

Sort each review in the export into one of four: a bug (a crash, a login failure, a payment error), a feature request, a
complaint about support or billing, or a praise or neutral note. Note the complaint patterns: the same point in several
reviews is a pattern; a single review is a single review.

- A bug goes to the product owner as a task with the review quoted and its date and device if the review gives one.
- A complaint about support or billing goes to the owner of that area as a task. The reply is drafted only after that owner
  answers.
- A review that threatens a legal claim, names a person, or reports a safety or payment problem goes to the owner as a
  question, with no draft.

## 4. Draft the replies

For each review that needs a reply, follow `skills/write-an-app-store-reply/SKILL.md`. Save each draft to
`knowledge/drafts/reply-<store>-<review-date>.md` with the review's id, the store, and the point the reply answers. Record
the status in `knowledge/reviews.md` as "drafted". A reply is never posted by this bot.

## 5. Choose three listing changes

Rank what you found by how much it would change a shopper's choice, and how little a person must do to publish it. For
each change record:
- the store and the element (title, subtitle, short description, keyword field, description, or a screenshot caption);
- the exact new text, saved to `knowledge/drafts/<store>-<element>-<date>.md`;
- the reason, with its number and its export;
- the keyword check, if a keyword is involved (`skills/check-a-keyword-before-using-it/SKILL.md`);
- the claim check: each number, award or ranking in the text has a dated source, or it is removed;
- the check that will show whether it worked, and the date to run it.

If a store test is the better route, propose it with `playbooks/change-a-listing.md` and name the store's own tool.

## 6. Write the review and publish it

Write `reports/YYYY-MM-DD-app-store.md` in the shape of `knowledge/examples/app-store-review.md`. Then:

    tico file publish reports/YYYY-MM-DD-app-store.md

Add the dated readings and the reply statuses to `knowledge/reviews.md`, add the drafted changes to `knowledge/changes.md`
as "proposed", note progress on the task, and commit this repository.

## 7. Close

    tico task done <id> -m "..." --version N

The note holds the headline, the report path, and what you could not read. Finish the task even on a quiet week.

## When a source fails

Name the failed export, store or review list in the report and in the closing note, with what that leaves unknown. A blocked
source is never "no change in the rating".
