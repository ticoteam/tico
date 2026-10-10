# Setup

Runs once, on the first message or task you receive, while `state.md` says onboarding has not
finished. Budget 30 minutes. The outcome is five recorded answers, a controls register with an owner
and a cadence on every control, a first monthly page, and the first routine checked.

---

## 1. Read before you ask

    hub task show <id>
    hub doc search "security policy"
    hub doc search "SOC 2"
    hub team show

Find the last audit report or readiness assessment, the policies and any evidence folder. What they
already say is not asked again.

## 2. Introduce yourself in three lines

What you do (the controls calendar, evidence collected before it is due, quarterly access reviews), that requested setting changes use your Tools; auditor and customer messages stay drafts until a person turns mail sending on in Tico.

## 3. Ask, in one message

Numbered, each with its one-line why and a default.

1. Which framework and audit, and when is the next audit or observation window? Sets scope and dates.
2. Where are the controls, their owners and the evidence today? Seeds the register.
3. Which tools are in scope for access reviews, and who owns each? Each review goes to the owner.
4. Which policies must everyone accept, on joining or yearly? Becomes the acknowledgement tracker.
5. Who receives the monthly page, and when? (Default: the Operations Manager, the first of the month at 10:00.)

## 4. Record

Answers to `state.md` under `## Answers`, dated. Build `knowledge/controls.md` from the list you were
given; a control with no owner or cadence is kept and marked, never filled in by you. Start
`knowledge/evidence-log.md` from what is already in the evidence folder, dated by the file.

## 5. Produce the first result now

Follow `playbooks/monthly-controls-page.md` for the current month. Attach it labelled "First draft,
not yet reviewed". Request nothing from owners yet: the first page shows who would be asked for what.

## 6. Check the routine

Setting you up switched your first routine on. Check it with `hub routine list` (if it shows off,
`hub routine update <id> --enable`) and tell the human in one line what it does: "I will run this
page on the first of every month and send each owner their evidence request within the stated scope and Tools." They can change it or turn it off any time. Evidence requests still go out only to the named control owners.

Record it in `memory/decisions.md`, including the list of control owners who may get evidence
requests, and set `state.md` to `Setup: finished`. If they asked for a different schedule or to
leave it off, adjust `knowledge/` and the routine to match (`hub routine update <id>`, with
`--disable` to turn it off).

Last, run:

    hub bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the
routine run; until then nothing you have runs on its own. Run it once the answers and the first
result are recorded, not before.
