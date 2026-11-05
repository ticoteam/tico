# Weekly hiring pipeline summary

Schedule: Fridays at 10:00 team time (routine `weekly-hiring-pipeline`), after setup. Also run by hand on request. Budget
30 minutes.

Inputs: `knowledge/pipeline.md`, every `knowledge/roles/<role>.md` for an open role, last week's page, and the open hiring
tasks (`hub task list --status open --status doing --status waiting`).

Outcome: one page for the hiring managers, in the shape of `knowledge/examples/hiring-pipeline.md`. It shows where each open
role stands, who is waiting on a human past the agreed wait, which replies are ready to send, and which kits need work.
Messages to outsiders stay drafts until a person turns mail sending on in Tico.

Done when every open role has a line, every candidate past the agreed wait is named with the human and the days, the page
is published, and the task note carries the headline.

---

## 1. Read where things stand

    hub task show <id>
    hub task list --status open --status doing --status waiting

Then read `knowledge/pipeline.md`, the role file for each open role, and last week's page.

## 2. Count by stage

For each role: applications received, summarised, with the manager, in interviews, at offer stage, closed. Use the stages a
human set. Never move a candidate yourself. A number with no source is left out.

## 3. Find who is waiting

List every candidate waiting on a human for longer than the agreed wait (default two working days), with the human and
the number of days. Managers read this line first.

## 4. Move candidates on

- **Replies owed.** For each candidate past the agreed wait, prepare the reply that the stage calls for: an
  acknowledgement, a next step, or a decline the manager has already recorded. Write it as a draft on the task. Send it
  only when the task asks for it, a granted Tool allows it, and a person has turned mail sending on in Tico. The content of
  any decision belongs to the manager.
- **Interviews.** For each candidate the manager moved to interview since last week, hand scheduling to
  `recruiting-coordinator` with `hub task create --owner recruiting-coordinator`: role, reference, rounds, and the panel
  from the role file. If that bot is not in `hub team show`, list the candidates for the hiring manager instead.

## 5. Check the kits

Each role in interviews needs a kit. A role without one, or an interviewer who scored against no written guide, is a flag
on the page.

## 6. Write the page and hand it over

Write `reports/YYYY-MM-DD-hiring-pipeline.md` in the shape of `knowledge/examples/hiring-pipeline.md`, then:

    hub file publish reports/YYYY-MM-DD-hiring-pipeline.md

Close the task with `hub task update <id> --status done --note "..."`: the headline, the counts, and what you could not read.

## When a source fails

Name the role or mailbox you could not read. A role with no data is "not read", never "quiet".
