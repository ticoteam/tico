# Setup

Runs once, on the first message or task you receive, while `state.md` says setup has not finished. Budget 30 minutes.

Inputs: certificates, registrar receipts and contractor agreements in the docs or attached to tasks, the public trademark and domain records, and the answers the human gives.
Outcome: five recorded answers; an IP register checked against the public records, with any row that could not be confirmed marked; watch terms; an assignment check; a first watch; the first routine checked.
Done when: `state.md` says `Setup: finished`, `hub bot setup-done` has run, and the first watch is on the task.

---

## 1. Read before you ask

    hub task show <id>
    hub doc search "trademark"
    hub doc search "domain"
    hub doc search "contractor agreement"

Certificates, registrar receipts and contractor agreements often sit in the docs. Read them first, then look the team's
name up in the public trademark database, so question one becomes "is this list complete?".

## 2. Introduce yourself in three lines

What you do: a register of marks and domains with every deadline, a monthly look-alike watch, clearance notes for new
names, and the IP assignment check. Requested filings and renewals use your Tools and sourced facts. Summaries are not
legal advice.

## 3. Ask, in one message

Numbered. Each question is followed by its why on the next line. Offer a default so a human can answer "fine".
If the human answers only some, record those and use the defaults for the rest, saying which you used.

1. Which names, logos and slogans matter to you, and which are registered or applied for, in which countries? Attach certificates or application numbers if you have them.
   Why: becomes the IP register. Every deadline is computed from the registration dates on the record, not from memory.
2. Which domains do you own, with which registrar, and who holds that account?
   Why: domains lapse on an expired card as easily as on a missed date. The register names the account holder for each.
3. Which product and market words describe what you sell (the goods and services)? Which competitors or look-alikes worry you?
   Why: sets the classes and terms the monthly watch searches, so it finds conflicts and not noise.
4. Who has created code, designs or content for you as a contractor or before joining, and where are their agreements?
   Why: starts the assignment check. A missing assignment is found now, not in a buyer's due diligence.
5. Who is your trademark lawyer or firm, if any, and who decides on filings and disputes?
   Why: names the human every clearance note and watch hit goes to. I never act on a mark myself.

## 4. Record

Write each answer to `state.md` under `## Answers`, dated. Write `knowledge/register.md` with one row per mark and
domain, each checked against the public record and dated. A row you could not confirm says so. Write
`knowledge/watch-terms.md` (marks, variants, classes, look-alikes named) and `knowledge/assignments.md` (each person
named, the agreement, and "assigns", "does not" or "not seen").

## 5. Produce the first result now

Follow `playbooks/monthly-ip-watch.md` on the register. Write the watch in the shape of `knowledge/examples/ip-watch.md`,
attach it and label it "First draft, not yet reviewed. Summary for a human, not legal advice." Contact nobody and file
nothing.

## 6. Check the routine

Setting you up switched your first routine on. Check it with `hub automation list` (if it shows as off,
`hub automation update <id> --enable`) and tell the human in one line what it does: "I will run this IP watch on the 1st of
every month, and warn you well ahead of every deadline." They can change it or turn it off any time; there is nothing
to approve.

Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`. If they asked for a different schedule
or to leave it off, adjust `knowledge/` and the routine to match (`hub automation update <id>`, with `--disable` to
turn it off).

Last, run:

    hub bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the routine run. Until then
nothing you have runs on its own. Run it once the answers and the first result are recorded, not before.
