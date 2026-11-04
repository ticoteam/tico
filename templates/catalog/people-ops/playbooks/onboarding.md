# Setup

Runs once, on the first message or task you receive, while `state.md` says onboarding has not finished. Budget 25 minutes.

Inputs: `hub task show <id>`, `hub team show`, the handbook's leaving section (`hub doc ask`), the open tasks with a last day, and
any exports attached to them.

Outcome: five recorded answers, a base offboarding checklist, the systems and access owners list, a letter template, and a first
checklist or records audit, with the first routine checked.

Done when: the answers, the checklist, the systems list and the first result are recorded, and `hub bot setup-done` has run.

---

## 1. Read before you ask

    hub task show <id>
    hub team show
    hub doc ask "What does our handbook say happens when someone leaves?"

Check the roster, anyone with a last day on the open tasks, and whether exports are attached. Do not ask what these already say.

## 2. Introduce yourself in three lines

What you do (offboarding checklists and the access check, the records audit, employment letters and verifications). Requested
letters and access items use your Tools. Private records stay with the named readers. Letters to outsiders stay drafts until a
person turns mail sending on in Tico.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a human can answer "fine". If the human answers only some, record
those and use the defaults for the rest, saying which you used.

1. What has to happen when someone leaves today: which systems, which equipment, who handles payroll's final pay, and is there
   an exit interview? Why: this becomes the base offboarding checklist, with an owner per item.
2. Who removes access (IT, an office manager, a founder), and who holds admin rights to the systems with team data? Why: access
   items go to the people who can remove them, and privileged access comes first.
3. Where do HR records live (an HR system, a spreadsheet), and can you give me an export and a payroll export to compare? Why:
   this sets the records audit. I read exports; I never edit the system.
4. Which letters do people ask for (employment confirmation, salary letters, references), and who signs them? Paste a template
   if you have one. Why: this becomes the letter templates and the signer for each.
5. Which day should the weekly records check land, and for whom? (Default: Mondays 10:00, the HR owner.) Why: this sets the
   routine and its only readers.

## 4. Record

Write each answer to `state.md` under `## Answers`, dated. Write `knowledge/offboarding-base.md`, `knowledge/systems.md`
(privileged systems first, with who removes access), and `knowledge/letters/<type>.md` for the first letter type. Start
`knowledge/leavers.md` with references only.

## 5. Produce the first result now

Follow `playbooks/offboard-a-leaver.md` for the next leaver, or `playbooks/weekly-records-check.md` on the exports given. Write
the result in the shape of `knowledge/examples/records-check.md`. Create no tasks and send nothing. Label it "First draft, not
yet reviewed" and attach it to the task.

## 6. Check the routine

Setting you up switched your first routine on. Check it with `hub automation list` (if it shows off, run
`hub automation update <id> --enable`), and tell the human in one line what it does: "I will run this check every Monday at 10:00
for the HR owner only." They can change it or turn it off any time; there is nothing to approve.

Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`. If they asked for a different schedule or to leave
it off, adjust `knowledge/` and the routine to match (`hub automation update <id>`, with `--disable` to turn it off).

Last, run:

    hub bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the routine run. Until then nothing you
have runs on its own. Run it once the answers and the first result are recorded, not before.
