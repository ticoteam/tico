# Write a pitch

Used when a task you own is in Pitch: your own idea from the weekly review, or a request a person, a bot or Listening
filed. Budget 15 minutes. The outcome is a pitch of 280 characters at most, as the task's body, that a person can OK,
park or turn down from the pitch alone.

---

## 1. Check that it needs a new pitch

Open `knowledge/pitches.md` and `tico task list --label area:<slug>`. If a task for this problem is open, add the new
evidence to that task as a comment and close this one as a duplicate:

    tico task comment <open task> "<evidence, its source and date>"
    tico task close <id> -m "Same problem as #<open task>" --version N

If an earlier pitch was turned down, read its reason first. Pitch again only with evidence the reason did not cover.

## 2. Keep what was asked

A request someone else filed keeps their words: copy the task's body into a comment, with who asked and when, before
you write the pitch over it.

    tico task comment <id> "Asked by <who>, <date>: <their words>"

## 3. Write it

280 characters at most, in this order: the problem in the customer's words, who has it (how many accounts, from what),
and the fix in one line. Plain words, no feature name standing in for the problem.

    Shared-calendar users can't connect (14 of 117 new Creators accounts, 9 tickets, 2 calls); their activation is 21%
    against 44%. Fix: read shared calendars through the delegated scope.

Count it before you send it: a longer one is refused. The evidence behind each number goes in a comment, with its
source and date, and in `knowledge/problems.md`.

## 4. Check it

- Every number has a source in the comment below it.
- Nothing in it promises a customer a fix, a feature or a date.
- No customer's name, email address or other personal detail. Name an account by its id.

## 5. File it

Your own idea, from the review:

    tico task create --owner <your slug> --pipeline product --title "<the problem, under ten words>" --body "<pitch>" --label area:<slug>

A request already in Pitch:

    tico task update <id> --body "<pitch>" -m "Pitch written" --version N

Leave out the label when you own the whole product. Record it in `knowledge/pitches.md`: the date, the problem, the
task id and "in Pitch".

## 6. Stop there

The stage asks a person: OK moves it to Spec, Not now snoozes it, No closes it. Do not ask them again with
`tico task ask`, and do not tell a customer, a salesperson or a marketer that the problem will be fixed. The next weekly
review records the decision.
