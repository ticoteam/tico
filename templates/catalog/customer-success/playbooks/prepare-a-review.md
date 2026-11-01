# Prepare a quarterly business review

Triggered by a task that names an account and a review date. Budget 45 minutes. The outcome is a
one-to-two page pack for the account owner. Nothing goes to the customer.

Inputs: the account note in `knowledge/accounts/<account>.md`, the renewal date in `knowledge/renewals.md`, the calls
and tickets since the last review, and the usage reading if one is in your access.

Done when: each number in the pack has a source and date or is marked unknown, the open issues each have an owner, the agenda
has five items at most, and the pack is published and attached to the task.

---

## 1. Read the account

    hub task show <id>

Read `knowledge/accounts/<account>.md`, the renewal date in `knowledge/renewals.md`, and the calls
(`hub meeting search "<account>"`) and tickets since the last review.

## 2. Show value in the customer's terms

List what they use most, what has changed since the last review and any outcome they told us about,
each with a date and source. Numbers only from a reading; if usage cannot be read, say so and use what
they said on calls.

## 3. Show risk and opportunity

Open issues with their age and owner; features they do not use but similar customers do (only from a
source you can cite); anything on the horizon for the renewal, with the notice deadline.

## 4. Draft the agenda and asks

Five items at most: outcomes so far, what is open, what is next, a question for the customer, and the
renewal timeline with a gap for any commercial term. Draft the follow-up note the owner might send.

## 5. Hand over

Write `reports/YYYY-MM-DD-<account>-review.md`, `hub file publish` it, attach it to the task, and `hub task update <id> --status done --note "..."`: the pack's headline and what you could not read.
