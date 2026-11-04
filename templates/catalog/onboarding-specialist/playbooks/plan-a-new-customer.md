# Plan a new customer

Triggered by a task naming a newly signed customer, or by the board finding one. Budget 20 minutes.

Inputs: the deal record (a CRM read, or the task), the order form or contract attached to it, the sales call notes, `knowledge/milestones.md`
and `knowledge/onboarding-practice.md`.

Outcome: `knowledge/customers/<customer>.md` with goals, promises, milestones and owners, and a kickoff agenda on the task.

Done when: all six handoff items are found, a gap or a conflict; every milestone has a date and an owner on both sides, or a gap;
every promise has its source; the plan and the agenda are attached to the task; and any promise that cannot be met as sold has its
question on the task.

---

## 1. Read the handoff

Follow `skills/check-a-handoff-before-kickoff/SKILL.md` on the deal record, the order form and any sales call (`hub meeting search "<customer>"`).
Write down, each with its source, who signed, who will use the product, what they are replacing, what they said success looks like,
and every promise made.

## 2. Size it and date it

Pick small, mid-size or large from `knowledge/milestones.md`. Set a target date for each standard milestone from the start date.
Add a milestone only if the deal promised something the standard list lacks. First value gets the earliest date you can defend.

## 3. Name owners on both sides

For each milestone, who does it at {{company_name}} and who at the customer. Unknown is a gap to fill at the kickoff, not a guess.

## 4. Check go-live before dating it

Before the go-live date is fixed, follow `skills/check-go-live-blockers/SKILL.md`. An open blocker goes on the task with its owner.

## 5. Draft the kickoff agenda

Thirty to forty-five minutes: their goals first (read back in their words), the plan and dates, what we need from them and by when,
how updates will reach them, and questions. Attach it to the task for whoever runs the call. Invitations go out only when the task
asks for them, a granted Tool allows it, and a person has turned mail sending on. Otherwise they stay drafts.

## 6. Hand over

Save the plan, attach it and the agenda to the task, and ask one question if a promise cannot be met as sold. Commit, then
`hub task update <id> --status done --note "..."`.
