# Tomorrow's dispatch plan

Schedule: weekdays at 15:00 team time (routine `tomorrows-dispatch-plan`), after setup. Budget 30 minutes. The outcome is tomorrow's plan by crew, the clash list and the
arrival notices, all ready for the requested dispatch work. Nothing is sent or changed.

---

## 1. Inputs

Tomorrow's jobs export on the task (ask once if missing), `knowledge/crews.md`, leave from calendars
where connected, and yesterday's plan with its close-out notes.

## 2. Assign

Follow "How you plan" in `AGENT.md`: hard limits, then area and route, then balance, then the promised
windows. Keep jobs already assigned and confirmed where they are unless that breaks a hard limit; a
move is a proposal with its reason.

## 3. Clashes

Double bookings, jobs with no qualified person free, a day over someone's hours, parts not on the van,
access not confirmed (keys, gate codes held by the customer), and windows the plan cannot keep. Each
with one or two options: swap with another crew, move to a free slot the customer already offered,
or call the customer.

## 4. Arrival notices

One per customer in the agreed form: the day, the window, the technician's first name, what to prepare
(clear access, a pet shut away). Attach the batch to the task; send requested notices with your Tools when a person has turned mail sending on in Tico, otherwise keep drafts.

## 5. Close-out check

Yesterday's jobs with no completion note, photos or sign-off, by technician.

## 6. Write and hand over

Write `reports/YYYY-MM-DD-dispatch.md` in the shape of `knowledge/examples/dispatch-plan.md`, `hub file
publish` it, commit, and `hub task update <id> --status done --note` with the headline.
