# Offboard a leaver

Triggered by a task naming a leaver and a last day. Budget 25 minutes.

Inputs: the task (role, team, manager, last day); `knowledge/offboarding-base.md`; `knowledge/systems.md`, which says who removes
access to each system; `knowledge/leavers.md`; and the payroll and benefits dates when they are on the task.

Outcome: a checklist with an owner and a date for every item, proposed for review, and a plan to confirm that access is gone.
You remove nothing yourself.

Done when: every item has an owner and a date, each access item has the confirmation it needs, the checklist is in
`knowledge/leavers.md`, and the access items are chased the day after the last day (see
`skills/check-leaver-access-is-gone/SKILL.md`).

---

## 1. Read the request

    hub task show <id>
    hub team show

Role, team, manager, last day. If the HR owner marked the departure sensitive (a dismissal, a dispute), build only what they ask
for, and record no reason anywhere.

## 2. Build the checklist

Start from `knowledge/offboarding-base.md` and cut what does not apply. Use `knowledge/offboarding-and-access.md` for why the
order matters.

- **Before the last day:** knowledge handover plan with the manager (open work, documents, customer and vendor contacts);
  shared passwords this person knows, listed for rotation; final pay information to the payroll owner; the benefits end date
  to `benefits`; exit interview offered; equipment return arranged.
- **Last day:** privileged access removed first (admin, finance, production, owner roles); single sign-on and mail disabled;
  other tools removed; devices returned; building access ended.
- **Day after:** each access owner confirms in writing; mail forwarding or an auto-reply, as policy allows.

Each item has what it is, its owner from `knowledge/systems.md`, and its due date.

## 3. Propose

Attach the checklist. Ask only for missing offboarding facts, with `hub task ask <id>`. Create one requested task per owner:
`hub task create --owner <owner> --title "<leaver ref>: <item>" --due <date> --parent <id>`, but only when the task asks for it
and a granted Tool allows it. Otherwise list the owners on the checklist for the HR owner to act on.

## 4. Confirm

The day after the last day, read each item's task. Follow `skills/check-leaver-access-is-gone/SKILL.md`. Anything without a
dated confirmation is chased once; privileged access goes straight to the HR owner. Update `knowledge/leavers.md`.

## 5. Close

When every item is confirmed, run `hub task update <id> --status done --note "..."` with the confirmation dates.
