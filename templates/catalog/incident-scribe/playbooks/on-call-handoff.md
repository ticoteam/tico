# On-call handoff

Triggered by a task at a rotation change (for example, "handoff from the outgoing on-call to the incoming
on-call") or by a human asking for one. Budget 20 minutes. The outcome is one page the incoming person reads
before their first page arrives. You do not page, silence an alert or change anything.

---

## Inputs

- `hub task show <id>` and `hub task list --status open --status doing --status waiting`.
- `knowledge/actions.md` and this week's incident files in `reports/incidents/`.
- Any alert or error source named in `knowledge/triggers.md` that you can read (a channel, an attached export).
- The shift's dates.

## Steps

1. **Read the shift.** Read the inputs above. Note the shift's dates.
2. **Write the handoff.** One line each, with the link and the date:
   - **Open now:** incidents not resolved, with severity, status and the next step.
   - **Watch:** alerts that fired more than twice without action (noise to tune, a proposal for the owner), and
     anything degraded but not paging.
   - **Changes landing this week:** releases and migrations from the release readiness checklist, if the team
     keeps one, each with its rollback note.
   - **Action items due** during the shift, with owners.
   - **Could not read:** any source you lacked.
   Describe systems and events. Never a person's fault.
3. **Hand over.** Write `reports/oncall/YYYY-MM-DD-handoff.md`, run `hub file publish` on it, commit, and run
   `hub task update <id> --status done --note` with the count of open incidents in the first line.

## Output

`reports/oncall/YYYY-MM-DD-handoff.md`: open incidents, watch items, changes this week, action items due, and
sources not read.

## Done when

- The open-incident count is the first line of the task note.
- Every line has its link and date.
- Each source you could not read is listed.
