# Plan a trip

Triggered by a task, for example "Dana needs to be in Denver for the studio expo, 14 to 16 October". Budget 25
minutes.

Inputs: the task (traveller, purpose, dates), `knowledge/policy.md`, `knowledge/preferences.md`, the traveller's calendar
where connected, and the two skills this plan uses: `skills/total-a-trip-cost/SKILL.md` and
`skills/check-entry-documents/SKILL.md`.

Outcome: `reports/trip-<traveller>-<date>.md` in the shape of `knowledge/examples/trip-plan.md`. Two or three options
with totals, the recommendation and why, the policy result, the entry documents to confirm and, once the traveller
picks, the booking request.

Done when: every option has a total and a policy result; the recommendation says why; the traveller is asked to pick
with `hub task ask`; the entry documents are listed with their sources; and the task note says what is still open.

---

## 1. Pin the trip down

    hub task show <id>

Traveller, purpose, where exactly (the venue address, not just the city), the first and last commitments there, and
anything fixed (a talk at 09:00 on the first day). Check the calendar if connected. Ask the traveller one question if
something is missing.

## 2. Find the options

Search public fares and hotel rates for the exact dates. Keep three at most: the cheapest within policy, the best for
the schedule, and the preferred supplier if it is within about 5 percent of the cheapest total. For each option record
the carrier or hotel, the times, the fare rules (refundable, change fee, bags), the price with the time it was seen,
and the distance to the venue. Total each one with `skills/total-a-trip-cost/SKILL.md`.

## 3. Check it

Against `knowledge/policy.md`: class, hotel limit per night, advance-booking minimum. Name any exception, with the
amount over and the reason. For a trip that crosses a border, run `skills/check-entry-documents/SKILL.md`. The
traveller confirms their own documents.

## 4. Recommend and ask

Write `reports/trip-<traveller>-<date>.md`. Ask the traveller to pick with `hub task ask <id>`. Record the option, its
total, its fare rules and its policy fit in `knowledge/trips.md`.

A booking follows the traveller's pick, and only with a granted Tool. If a fact or a Tool is missing, name it in the
task rather than working around it.

## 5. After it is booked

File the confirmation numbers in `knowledge/trips.md`. Build the itinerary: local times, addresses, check-in rules, the
local contact, and what to do if a leg is cancelled. Send it to the traveller with
`hub message send --fyi <traveller> "<one line and the link>"`.
