# Weekly engineering summary

Schedule: Mondays at 09:00 team time (routine `weekly-engineering-summary`), after setup. Also run by hand on request.
Budget 40 minutes. The outcome is one page for the engineering manager: what shipped, what is stuck, what broke
and what is blocked, with the evidence. Nothing is sent to anyone else, and nothing on GitHub changes.

---

## Inputs

- `knowledge/areas.md`, `knowledge/measures.md`, and last week's summary, so you report movement, not what merely exists.
- The repositories in scope, as listed in `state.md`, read through `gh`.
- The engineering bots' latest reports, and the open tasks on them: `hub task list --status open --status doing --status waiting`.

## Steps

1. **Read where things stand.** `hub task show <id>`, then the inputs above.
2. **Read GitHub, per repository in scope.**

       gh pr list -R <repo> --state merged --search "merged:>YYYY-MM-DD" --json number,title,mergedAt,url
       gh pr list -R <repo> --state open --json number,title,author,createdAt,reviewDecision,statusCheckRollup
       gh run list -R <repo> --branch main --limit 20

   Record: merged this week, open pull requests with age in working days and review state, and whether the main
   branch was red for more than a day. Apply the thresholds in `knowledge/measures.md`. A pull request with no
   review after the review threshold, or open past the open threshold, is stuck.
3. **Read the team's own output.** For each engineering bot in scope, take one line: its headline and the path of
   its latest report. A bot that produced nothing is named as such, not omitted.
4. **Compute only the measures the human chose.** Follow `skills/measure-delivery-without-ranking-people/SKILL.md`.
   A measure you cannot compute is "not measurable from the sources I have".
5. **Write the summary** in the shape of `knowledge/examples/engineering-summary.md`, to `reports/`. Then run
   `hub file publish reports/YYYY-MM-DD-engineering-summary.md`.
6. **Propose routing, do not act.** For any unowned request or stuck item, write a routing proposal using
   `playbooks/route-a-request.md`. Nothing is assigned. Share only as the task and Tools allow. Messages to
   outsiders stay drafts until a person turns mail sending on in Tico.
7. **Finish.** Commit, then `hub task update <id> --status done --note`: the headline, the path, and which sources
   you could not read. Always finish it: an open scheduled task absorbs the next one.

## Output

`reports/YYYY-MM-DD-engineering-summary.md`, in this order: headline, what needs the manager, what shipped, stuck
pull requests (number, age, owner from `areas.md`, link), incidents, docs and release status, blocked work,
proposed routing, and what you could not read.

## Done when

- Every repository in scope is read, or is named as unread.
- Every stuck item has an owner from `areas.md`, or is marked "no owner" with a routing proposal.
- Every measure has its source and period, or says "not measurable".
- The summary is published, and the task is closed with a note.

## When a source fails

Name the repository or report, say what is therefore unknown, and keep going with the rest. A summary built from two
of four repositories that reads like a complete picture is worse than none.
