# Weekly engineering summary

Schedule: Mondays at 09:00 team time (routine `weekly-engineering-summary`), after setup. Also run by hand on request. Budget 40 minutes. The outcome is one page for the engineering
manager: what shipped, what is stuck, what broke and what is blocked, with the evidence. Nothing is sent to
anyone else and nothing on GitHub changes.

---

## 1. Read where things stand

    hub task show <id>

Then `knowledge/areas.md`, `knowledge/measures.md` and last week's summary, so you report movement, not
what merely exists.

## 2. Read GitHub, per repository in scope

    gh pr list -R <repo> --state merged --search "merged:>YYYY-MM-DD" --json number,title,mergedAt,url
    gh pr list -R <repo> --state open --json number,title,author,createdAt,reviewDecision,statusCheckRollup
    gh run list -R <repo> --branch main --limit 20

Record: merged this week, open pull requests with age in working days and review state, and whether the main
branch was red for more than a day. Apply the thresholds in `knowledge/measures.md`: a pull request with no
review after the review threshold, or open past the open threshold, is stuck.

## 3. Read the team's own output

`hub task list --status open --status doing --status waiting` for tasks on the engineering bots, and their
latest reports: the Senior Software Engineer's review queue, the QA Engineer's issue digest, the Release
Manager's notes, the Site Reliability Engineer's incident review, the Technical Writer's drift report, the
Security Engineer's advisory report and the DevOps Engineer's CI health report. Take one line from each: the headline and the path. A bot that produced nothing
is named as such, not omitted.

## 4. Compute only the measures the human chose

From `knowledge/measures.md`: shipped count, review wait (median and longest), stuck pull requests, and, where
releases or deploys can be read, lead time from first commit to release, deploy frequency, failed deploys and
time to restore. Show each with its source and the period. A measure you cannot compute from what you read is
"not measurable from the sources I have", never an estimate. Never by person.

## 5. Write the summary

`reports/YYYY-MM-DD-engineering-summary.md` in the shape of `knowledge/examples/engineering-summary.md`:
headline, what needs the manager, what shipped, stuck pull requests (number, age, owner from `areas.md`,
link), incidents, docs and release status, blocked work, proposed routing, and what you could not read.
Then `hub file publish reports/YYYY-MM-DD-engineering-summary.md`.

## 6. Propose routing, do not act

For any unowned request or stuck item, write a routing proposal in the summary using
`playbooks/route-a-request.md`. Nothing is assigned. Share with the intended audience within the requested work and Tools; messages to outsiders stay drafts until a person turns mail sending on in Tico.

## 7. Finish

Commit, then `hub task update <id> --status done --note`: the headline, the path, and which sources you could
not read. Always finish it: an open scheduled task absorbs the next one.

## When a source fails

Name the repository or report, say what is therefore unknown, keep going with the rest. A summary built from
two of four repositories that reads like a complete picture is worse than none.
