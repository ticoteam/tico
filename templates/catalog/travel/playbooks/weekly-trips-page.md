# Weekly trips page

Schedule: Mondays at 10:00 team time (routine `weekly-trips-page`), after setup. Budget 20 minutes. Nothing is booked
by this page.

Inputs: `knowledge/trips.md`, open tasks that mention travel, calendars where connected (events in another city,
"flight", "hotel"), and `knowledge/policy.md`.

Outcome: `reports/YYYY-MM-DD-trips.md`: every trip in the next 30 days with its status, the trips inside the
advance-booking window that are still unbooked, and booked spend against policy.

Done when: every trip in the next 30 days has a status; every unbooked trip inside the window is flagged with the days
left; spend is listed against policy; the page is published with `hub file publish`; and the task note carries the
headline: trips in 30 days, unbooked inside the window, and waiting on facts or Tools.

---

## 1. Collect the trips

`knowledge/trips.md`, open tasks that mention travel, and calendars where connected. A trip on a calendar with no
request is added as "not requested".

## 2. Status per trip

- **Planned**: options ready, waiting for the traveller's pick.
- **Waiting on facts or Tools**: the exact dependency and the days it has waited.
- **Booked**: confirmations filed.
- **Documents to confirm**: entry requirements the traveller has not yet confirmed.
- **Not requested**: on a calendar, with no request.

A trip inside the advance-booking window and not booked is flagged with the days left and the fare when it was last
checked.

## 3. Spend against policy

Booked trips this month: the total, and any booked outside policy with its documented reason. Never a judgement of a
person. A pattern, such as hotels in one city always over the limit, becomes a policy question for the owner.

## 4. Write and hand over

Write `reports/YYYY-MM-DD-trips.md`, publish it with `hub file publish reports/YYYY-MM-DD-trips.md`, commit, then
finish the task with `hub task update <id> --status done --note` and the headline.
