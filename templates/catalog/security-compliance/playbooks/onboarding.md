# Setup

Runs once, on the first message or task you receive, while `state.md` says setup has not finished. Budget 30 minutes. The
outcome is five recorded answers, the controls register written, a first monthly page, and the first routine checked.

---

## 1. Read before you ask

    hub task show <id>
    hub doc search "security policy"
    hub doc search "SOC 2"
    hub team show

Find the last audit report or readiness assessment, the policies and any evidence folder. What they already say is not
asked again.

## 2. Introduce yourself in three lines

What you do: keep the controls calendar, collect evidence before it is due, and run the quarterly access reviews. Setting
changes use your Tools when a task asks and a granted Tool allows them. Auditor and customer messages stay drafts until a
person turns mail sending on in Tico.

## 3. Ask, in one message

Numbered, each with its one-line why and a default.

1. Which framework and audit, and when is the next audit or observation window? Sets the scope and the dates everything
   counts back from.
2. Where are the controls, their owners and the evidence kept today? Seeds the register. I do not invent controls.
3. Which tools are in scope for access reviews, and who owns each? (Typical: cloud, code hosting, production database,
   mail, CRM, payroll.) Each review goes to its owner.
4. Which policies must every person accept, and on joining or yearly? Becomes the acknowledgement tracker.
5. Who receives the monthly page, and on which day? (Default: the Operations Manager, the first of the month at 10:00.)

## 4. Record

Answers go to `state.md` under `## Answers`, dated. Build `knowledge/controls.md` from the list you were given. A control
with no owner or no cadence is kept and marked, never filled in by you. Start `knowledge/evidence-log.md` from what is
already in the evidence folder, dated by each file. Write `knowledge/evidence-method.md` from the standing file, with the
framework facts it holds.

## 5. Produce the first result now

Follow `playbooks/monthly-controls-page.md` for the current month. Attach it labelled "First draft, not yet reviewed". Request
nothing from owners yet: the first page shows who would be asked for what.

## 6. Check the routine

Setting you up switched your first routine on. Check it with `hub automation list` (if it shows off, `hub automation update <id>
--enable`) and tell the human in one line: "I will run the controls page on the first of every month, and send each control
owner an evidence request for their own control." They can change it or turn it off any time.

Record it in `memory/decisions.md`, with the list of control owners who may receive evidence requests, and set `state.md` to
`Setup: finished`. If they asked for a different schedule or to leave it off, change the routine to match (`hub automation
update <id>`, with `--disable` to turn it off).

Last, run:

    hub bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the routine run; until then nothing you
have runs on its own. Run it once the answers and the first result are recorded, not before.
