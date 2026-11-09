# Weekly bookings page

Schedule: Mondays at 08:00 team time (routine `weekly-bookings-page`), after setup. Budget 30 minutes. Nothing is sent,
paid, moved or changed. The page goes to the Operations Manager (`ops-manager`) or the person the owner names.

Inputs: the bookings export and any reschedule requests on the task (ask once if the export is missing),
`knowledge/booking-rules.md`, `knowledge/exceptions.md`, and last week's page.

Outcome: one page in the shape of `knowledge/examples/bookings-page.md`: the headline, clashes, reschedule requests with
options, no-shows and late cancellations with drafts, rule questions, and what could not be read.

Done when every booking in the export has been checked against the rules, every clash is named with both bookings, every
reschedule request has options or a named blocking rule, every no-show has a draft or a reason it has none, the page is
published, and the task note carries the headline.

---

## 1. Read the inputs

Read the export, the requests and the rules. Check the export's date and its time zone. An export older than two days, or
with no time zone, is named on the page before any clash is counted.

## 2. Check the schedule

Follow `skills/check-a-schedule-for-clashes/SKILL.md` for the week. Mark every clash: double bookings, bookings outside
opening hours, bookings without the required buffer, and open slots that a rule makes unusable. Count the open slots that
can be sold within the rules.

## 3. Work the reschedule requests

For each request, follow `skills/offer-reschedule-options/SKILL.md`. Two or three slots that fit the rules and the
customer's limits. Where none fits, name the rule and who can grant an exception.

## 4. Follow up the no-shows

List the no-shows and late cancellations since last week, from the same definition every week. For each, follow
`skills/write-a-no-show-follow-up/SKILL.md` and draft the message. Drafts only.

## 5. Record the rules that bent

Any rule that was unclear, or needed an exception this week, goes in `knowledge/exceptions.md` with the date, the person who
decided it and why. A rule that needed an exception twice is a question for the owner, not a quiet change.

## 6. Write and hand over

Write `reports/YYYY-MM-DD-bookings-page.md`, then:

    hub file publish reports/YYYY-MM-DD-bookings-page.md

Add the week's no-show count and the source counts to `knowledge/no-show-patterns.md`. Commit, then:

    hub task update <id> --status done --note "..."

The note holds the headline (bookings this week, clashes, requests waiting), the page path and what could not be read.
Finish the task on a quiet week too.
