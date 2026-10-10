# Weekly hiring pipeline summary

Schedule: Fridays at 10:00 team time (routine `weekly-hiring-pipeline`), after setup. Also run by hand on request. Budget 30 minutes. The outcome is one page for the hiring
managers: where each open role stands, who is waiting on a human, and which replies are ready to go. Messages to outsiders stay drafts until a person turns mail sending on in Tico.

---

## 1. Read where things stand

    hub task show <id>
    hub task list --status open --status doing --status waiting

Then `knowledge/pipeline.md`, every `knowledge/roles/<role>.md` for open roles, and last week's page.

## 2. Count by stage

For each role: applications received, summarised, with the manager, in interviews, offer stage, closed.
Use the stages a human set; never move a candidate yourself. A number with no source is left out.

## 3. Find who is waiting

List every candidate waiting on a human longer than the agreed wait (default two working days), with the
human and the days. This is the line managers read first.

## 4. Move candidates on

- **Replies owed.** For every candidate past the agreed wait, prepare the reply the stage calls for (an
  acknowledgement, a next step, or a decline the manager already made) and send requested replies with your Tools when a person has turned mail sending on in Tico; otherwise keep drafts. Never decide the content of a decision.
- **Interviews.** For candidates the manager moved to interview since last week, hand scheduling to
  `recruiting-coordinator` with `hub task create --owner recruiting-coordinator` (role, reference, rounds,
  panel from the role file). Without that bot, list them for the hiring manager.

## 5. Check the kits

Each role in interviews has a kit. A role without one, or with an interviewer who scored differently
against no guide, is a flag on the page.

## 6. Write the page and hand it over

Write `reports/YYYY-MM-DD-hiring-pipeline.md` in the shape of `knowledge/examples/hiring-pipeline.md`.

    hub file publish reports/YYYY-MM-DD-hiring-pipeline.md

Then `hub task update <id> --status done --note`: the headline, counts, and what you could not read.

## When a source fails

Name the role or mailbox you could not read; a role with no data is "not read", never "quiet".
