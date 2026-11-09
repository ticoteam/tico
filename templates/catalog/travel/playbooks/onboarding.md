# Setup

Runs once, on the first message or task you receive, while `state.md` says onboarding has not
finished. Budget 20 minutes. The outcome is five recorded answers, the travel policy written down, a
first weekly trips page, and the first routine checked.

---

## 1. Read before you ask

    hub task show <id>
    hub doc search "travel policy"
    hub task list --status open
    hub calendar list

Trips already mentioned in tasks or on calendars are the first rows of `knowledge/trips.md`.

## 2. Introduce yourself in three lines

What you do (trip plans within policy, bookings prepared for review, itineraries, the trips
calendar), that requested bookings use your Tools and the travel policy; never keep passport or card numbers.

## 3. Ask, in one message

Numbered, each with its one-line why and a default.

1. Do you have a travel policy? If not: class of travel, hotel limit per city, how far ahead to book. Every option is checked against it.
2. Who owns travel bookings, and what budgets apply? (Default: the manager; above 2,000, the Operations Manager.)
3. How do you book today: a tool, an agent, or each human? Sets where a requested booking goes.
4. Which cities and events come up most; any preferred airlines or hotels? Preferred options go first when within about 5 percent of the cheapest total.
5. When should the weekly trips page land, and for whom? (Default: the Operations Manager, Mondays 10:00.)

## 4. Record

Answers to `state.md` under `## Answers`, dated. Write `knowledge/policy.md`; with no written policy,
write the answers as the working policy and mark it "working policy, sources recorded" until the owner
says otherwise. Default advance-booking rule if none is given: 14 days domestic, 21 international. That is a minimum,
not a target; `knowledge/travel-practice.md` has the fare data.

## 5. Produce the first result now

Follow `playbooks/weekly-trips-page.md`. If a trip is already requested, plan it with
`playbooks/plan-a-trip.md` too. Attach both labelled "First draft, not yet reviewed". Book nothing.

## 6. Check the routine

Setting you up switched your first routine on. Check it with `hub automation list` (if it shows off,
`hub automation update <id> --enable`) and tell the human in one line what it does: "I will send this page every Monday at 10:00 and plan each trip as it is requested." They
can change it or turn it off any time; there is nothing to approve.

Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`. If they asked for a
different schedule or to leave it off, adjust `knowledge/` and the routine to match
(`hub automation update <id>`, with `--disable` to turn it off).

Last, run:

    hub bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the
routine run; until then nothing you have runs on its own. Run it once the answers and the first
result are recorded, not before.
