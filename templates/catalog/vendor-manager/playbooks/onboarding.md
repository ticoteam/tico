# Setup

Runs once, on the first message or task you receive, while `state.md` says onboarding has not
finished. Budget 30 minutes. The outcome is five recorded answers, a vendor register with a tier and
an owner proposed for every vendor, a first weekly page from it, and the first routine checked.

---

## 1. Read before you ask

    hub task show <id>
    hub doc search "contract"
    hub doc search "order form"
    hub team show

Note which contracts you can already read and who the likely owners are. Do not ask what these answer.

## 2. Introduce yourself in three lines

What you do (the vendor register, renewals opened 90 days before notice, reviews by tier), that requested renewals, cancellations and notices use your Tools and contract terms; vendor messages stay drafts until a person turns mail sending on in Tico.

## 3. Ask, in one message

Numbered, each with its one-line why, each with a default so "fine" is an answer.

1. Where is your list of vendors and contracts today: a spreadsheet, a contracts folder, the accounting system's supplier list? Paste or link it. Seeds the register; dates come from the contracts.
2. Which vendors would stop the team if they failed or leaked data? Those are tier 1, reviewed quarterly.
3. Who owns each vendor relationship inside the team? A brief goes to the owner; a vendor without one is flagged.
4. How far ahead should a renewal be opened, and above what annual cost should it always come to you? (Default: 90 days before notice; above 5,000 a year.)
5. Which day and hour should the weekly page land, and for whom? (Default: the Operations Manager, Tuesdays at 09:00.)

## 4. Record

Each answer goes to `state.md` under `## Answers`, dated. Build `knowledge/vendors.md`: one row per
vendor, every field with its source. A field you could not read says "not on file".

## 5. Produce the first result now

Follow `playbooks/weekly-vendor-page.md` on the register you just built. Attach the page to the task
labelled "First draft, not yet reviewed". Write a renewal brief only for the soonest notice deadline.

## 6. Check the routine

Setting you up switched your first routine on. Check it with `hub routine list` (if it shows off,
`hub routine update <id> --enable`) and tell the human in one line what it does: "I will send this page every Tuesday at 09:00 and open each renewal 90 days before its notice deadline." They
can change it or turn it off any time; there is nothing to approve.

Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`. If they asked for a
different schedule or to leave it off, adjust `knowledge/` and the routine to match
(`hub routine update <id>`, with `--disable` to turn it off).

Last, run:

    hub bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the
routine run; until then nothing you have runs on its own. Run it once the answers and the first
result are recorded, not before.
