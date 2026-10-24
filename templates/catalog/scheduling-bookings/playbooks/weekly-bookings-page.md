# Weekly bookings page

Schedule: mondays at 08:00 team time (routine `weekly-bookings-page`), after setup. Budget 30 minutes. The outcome is the report below, ready for a
person to act on. Nothing is sent, paid, signed or changed.

---

## 1. Inputs

The bookings export and any reschedule requests on the task (ask once if missing), `knowledge/booking-rules.md`, and last week's page.

## 2. Work

1. List this week's bookings and the open slots; mark every clash: double bookings, bookings outside hours or without the required buffer.
2. For each reschedule request, find two or three slots that fit the rules and the customer's stated limits. If none fit, say which rule blocks and who can grant an exception.
3. List no-shows and late cancellations since last week and draft a follow-up for each in the team's voice, stating the fee only as the rules do. Drafts only.
4. Note any rule that was ambiguous or needed an exception, in `knowledge/exceptions.md`.
5. Write the page with the answer first: bookings this week, clashes, requests waiting.

## 3. Write and hand over

Write `reports/YYYY-MM-DD-bookings-page.md` in the shape of `knowledge/examples/bookings-page.md`, `hub file
publish` it, commit, and `hub task update <id> --status done --note` with the headline.
