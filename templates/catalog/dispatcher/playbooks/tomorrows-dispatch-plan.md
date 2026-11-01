# Tomorrow's dispatch plan

Schedule: weekdays at 15:00 team time (routine `tomorrows-dispatch-plan`), after setup. Budget 30 minutes.
Outcome: tomorrow's plan by crew, the clash list and the arrival notices, drafted for the dispatcher to review.
Nothing is sent or changed.

Done when: every job has a crew and a pass, or a named clash, on each check; every customer has a notice drafted or is
on the clash list; the close-out list is in the report; the report is published; and the task note carries the headline
and the clashes.

---

## 1. Inputs

Tomorrow's jobs export on the task (ask once if missing), `knowledge/crews.md`, leave from calendars where connected,
and yesterday's plan with its close-out notes.

## 2. Check and assign

Run `skills/check-a-job-before-you-assign-it/SKILL.md` on each job. Place the jobs that pass on the route, in the order
and with the buffers set out under "How you plan" in `AGENT.md`. Keep jobs already assigned and confirmed where they are,
unless that breaks a hard limit; a move is a proposal with its reason. Where a planned length is in question, settle it
with `skills/estimate-a-job-length-from-history/SKILL.md` before you use it.

## 3. Clashes

Double bookings, jobs with no qualified person free, a day over someone's hours, parts not on the van, access not
confirmed (keys, gate codes held by the customer), and windows the plan cannot keep. Each clash gets one or two options,
the one you recommend first: swap with another crew, move to a free slot the customer already offered, or call the
customer.

## 4. Arrival notices

One per customer, written with `skills/write-an-arrival-notice/SKILL.md`: the day, the window, the technician's first
name, what to prepare. Attach the batch to the task. Notices stay drafts until a person turns mail sending on in Tico.
A send needs the task to ask for it and a granted Tool to allow it.

## 5. Close-out check

Yesterday's jobs with no completion note, photos or sign-off, by technician. A job with no close-out is listed as open,
not as done.

## 6. Write and hand over

Write `reports/YYYY-MM-DD-dispatch.md` in the shape of `knowledge/examples/dispatch-plan.md`, publish it with
`hub file publish reports/YYYY-MM-DD-dispatch.md`, commit, and `hub task update <id> --status done --note` with the
headline.
