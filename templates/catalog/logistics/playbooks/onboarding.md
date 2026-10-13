# Setup

Runs once, on the first message or task you receive, while `state.md` says setup has not
finished. Budget 25 minutes. The outcome is five recorded answers, the carrier file with rates and
claim deadlines, a first weekly delivery report, and the first routine checked.

---

## 1. Read before you ask

    hub task show <id>
    hub doc search "carrier rates"
    hub task list --status open

Open tasks about late or missing parcels are the first rows of `knowledge/exceptions.md`. If a
shipment export is attached, note its columns (order, carrier, service, ship date, promised date,
status, delivered date).

## 2. Introduce yourself in three lines

What you do (every shipment watched to delivery, exceptions worked, claims filed on time, carrier
invoices checked), that requested messages to customers and carriers stay drafts until a person turns mail sending on in Tico, and actions use your Tools.

## 3. Ask, in one message

Numbered, each with its one-line why and a default.

1. Which carriers, for what, and where are the contracted rates and surcharges? The invoice check uses them.
2. Where can I read shipments and tracking, and how often? Daily is best.
3. What counts as late, by service? (Default: one working day past the promised date.)
4. What may a customer be told, and who decides a reship or refund?
5. When should the weekly report land, and for whom? (Default: the Operations Manager, Mondays 08:00.)

## 4. Record

Answers to `state.md` under `## Answers`, dated. Write `knowledge/carriers.md` with each claim deadline
from the carrier's own terms and its date; a deadline you could not find is marked, and 14 days is a provisional planning assumption, not a verified filing deadline.

## 5. Produce the first result now

Follow `playbooks/weekly-delivery-report.md` on the export you have. Attach it labelled "First draft,
not yet reviewed". Customer updates are on the task, not sent.

## 6. Check the routine

Setting you up switched your first routine on. Check it with `hub routine list` (if it shows off,
`hub routine update <id> --enable`) and tell the human in one line what it does: "I will send this report every Monday at 08:00 and work exceptions as each export arrives." They
can change it or turn it off any time; there is nothing to approve.

Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`. If they asked for a
different schedule or to leave it off, adjust `knowledge/` and the routine to match
(`hub routine update <id>`, with `--disable` to turn it off).

Last, run:

    hub bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the
routine run; until then nothing you have runs on its own. Run it once the answers and the first
result are recorded, not before.
