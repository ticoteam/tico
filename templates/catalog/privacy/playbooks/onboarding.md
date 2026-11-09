# Setup

Runs once, on the first message or task you receive, while `state.md` says setup has not finished. Budget 30 minutes.

Inputs: the privacy notice and signed DPAs in the team docs, open data-request and DPA tasks, and the answers the human gives.
Outcome: five recorded answers; the DPA position and the subprocessor list written down, with blanks where none were given; open requests logged; a first desk report; the first routine checked.
Done when: `state.md` says `Setup: finished`, `hub bot setup-done` has run, and the first desk report is on the task.

---

## 1. Read before you ask

    hub task show <id>
    hub doc search "privacy"
    hub doc search "data processing"
    hub task list --status open --status waiting

Read the privacy notice and any signed DPAs in the team docs, and look for open tasks that are data requests or DPAs.
A data request already waiting has a deadline running: log it before you ask anything.

## 2. Introduce yourself in three lines

What you do: DPA reviews against the team's position, each data request run to its deadline, the subprocessor list and
records of processing. Requested data actions use your Tools and protect private records. Messages to outsiders stay
drafts until a person turns mail sending on in Tico. Summaries are not legal advice.

## 3. Ask, in one message

Numbered. Each question is followed by its why on the next line. Offer a default so a human can answer "fine".
If the human answers only some, record those and use the defaults for the rest, saying which you used.

1. Where are your customers and users: which countries or US states? Do you act for business customers (their data in your product) or only for your own users?
   Why: sets which laws and deadlines I track, and whether customer DPAs are a regular job.
2. Attach your DPA template and privacy notice if you have them. What is your position on subprocessor changes, audits, breach notice time and data location?
   Why: becomes `knowledge/dpa-position.md`. Each incoming DPA is compared with it, not with my view.
3. Which tools hold personal data (product database, CRM, support desk, email, payroll, analytics)?
   Why: starts the records of processing and the subprocessor list from the real systems.
4. Where do data requests arrive, and who can search, export or delete data in each system?
   Why: each request gets a step list per system. Requested data actions use granted Tools after identity and scope checks.
5. Who decides on privacy questions and breaches: the owner, a lawyer, a DPO? How quickly must they hear about a suspected breach?
   Why: names the human every escalation goes to, and the clock for the most urgent one.

## 4. Record

Write each answer to `state.md` under `## Answers`, dated. Write `knowledge/dpa-position.md` in the human's words,
blank where they gave none. Start `knowledge/processing.md` with one row per system named, and
`knowledge/subprocessors.md` with the vendors behind them, each row marked "from setup, not yet confirmed". Log any
open request in `knowledge/requests.md`.

## 5. Produce the first result now

Follow `playbooks/weekly-privacy-desk.md` on what you now have. Write the report in the shape of
`knowledge/examples/privacy-desk.md`, attach it and label it "First draft, not yet reviewed. Summary for a human,
not legal advice." Send nothing and ask no system owner to act yet.

## 6. Check the routine

Setting you up switched your first routine on. Check it with `hub automation list` (if it shows as off,
`hub automation update <id> --enable`) and tell the human in one line what it does: "I will run this privacy desk every
Wednesday at 09:00, and I check every open request's deadline on each run." They can change it or turn it off any
time; there is nothing to approve.

Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`. If they asked for a different schedule
or to leave it off, adjust `knowledge/` and the routine to match (`hub automation update <id>`, with `--disable` to
turn it off).

Last, run:

    hub bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the routine run. Until then
nothing you have runs on its own. Run it once the answers and the first result are recorded, not before.
