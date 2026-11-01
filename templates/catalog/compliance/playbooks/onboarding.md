# Setup

Runs once, on the first message or task you receive, while `state.md` says setup has not finished. Budget 30 minutes.

Inputs: formation documents, licences, insurance policies and past filings in the team docs or attached to tasks, the calendar, and the answers the human gives.
Outcome: five recorded answers; a register of obligations built from where the team is registered and what it does, with blanks where a rule is not yet confirmed; a first calendar; the first routine checked.
Done when: `state.md` says `Setup: finished`, `hub bot setup-done` has run, and the first calendar is on the task.

---

## 1. Read before you ask

    hub task show <id>
    hub doc search "annual report"
    hub doc search "licence"
    hub doc search "insurance"
    hub calendar list

Formation documents, licences and past filings often already sit in the docs; read them before you ask. If they name
the registered places or the licences, question one or two becomes "is this complete?".

## 2. Introduce yourself in three lines

What you do: a register of every filing, licence and renewal the team carries, a weekly calendar with owners, and a
filing pack before each deadline. Requested filings use your Tools, and every fact is sourced. Summaries are not
legal advice.

## 3. Ask, in one message

Numbered. Each question is followed by its why on the next line. Offer a default so a human can answer "fine".
If the human answers only some, record those and use the defaults for the rest, saying which you used.

1. Where is the team incorporated or registered, and in which other states, provinces or countries does it have staff, an office or registered sales?
   Why: each place brings its own annual report, registered agent and tax registration. The register starts from this list.
2. Which licences, permits or regulated activities does the team depend on (a trade licence, a food permit, a money or health rule, an export control)?
   Why: these are the obligations whose lapse stops the team, so they are listed first and watched earliest.
3. Which insurance policies do you hold, and when do they renew?
   Why: renewals need broker questionnaires weeks ahead, so they go on the calendar with that lead time.
4. Who files today, and who has the credentials: the owner, an accountant, a registered agent service, a lawyer?
   Why: every calendar line names the human who files. I prepare the pack; they submit it.
5. How early do you want to hear about a deadline? (Default: 60 days ahead, then 14 days, then 3 days.)
   Why: sets the lead times the calendar warns at, and what counts as urgent.

## 4. Record

Write each answer to `state.md` under `## Answers`, dated. Build `knowledge/register.md`: for each place of registration,
the annual report and registered agent; for each licence, permit and policy named, its renewal. Write the due-date rule
and its source for each row, or "rule not yet confirmed". Put every place or activity nobody confirmed into
`knowledge/unchecked.md`.

## 5. Produce the first result now

Follow `playbooks/weekly-compliance-calendar.md` on the register. Write the calendar in the shape of
`knowledge/examples/compliance-calendar.md`, attach it and label it "First draft, not yet reviewed. Summary for a human,
not legal advice." File nothing and create no tasks yet.

## 6. Check the routine

Setting you up switched your first routine on. Check it with `hub automation list` (if it shows as off,
`hub automation update <id> --enable`) and tell the human in one line what it does: "I will send you this compliance
calendar every Tuesday at 09:00, and prepare a filing pack before each deadline." They can change it or turn it off
any time; there is nothing to approve.

Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`. If they asked for a different schedule
or to leave it off, adjust `knowledge/` and the routine to match (`hub automation update <id>`, with `--disable` to
turn it off).

Last, run:

    hub bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the routine run. Until then
nothing you have runs on its own. Run it once the answers and the first result are recorded, not before.
