# Weekly ops checklist and team summary

Schedule: Mondays at 08:30 team time (routine `weekly-ops-checklist`), after setup. Also run by hand on request. Budget 35 minutes. The outcome is one page for the recipient:
what is overdue, what is due, what is blocked, what the Operations bots produced, and what to route. Nothing
is sent, assigned or changed.

Inputs: `knowledge/rhythm.md`, `knowledge/duties.md`, `knowledge/vendors.md`, last week's page in `reports/`,
the open tasks, the calendar for the next 7 days, and the reports the Operations bots published since last Monday.

Done when: every register row has a state (or "needs a date" or "needs an owner"); the checklist has five to nine
items; each quiet vendor has a follow-up attached or is named with the reason it has none; the page is published;
the recipient has its one-line message; and the task is done with its counts.

---

## 1. Read where things stand

    hub task show <id>

Then `knowledge/rhythm.md`, `knowledge/duties.md`, `knowledge/vendors.md` and last week's page in
`reports/`. Read live sources, not memory:

    hub task list --status open --status doing --status waiting
    hub update list --kind weekly
    hub calendar list

Skim the reports the Operations bots published since last Monday (`meeting-notes`, `procurement`,
`vendor-manager`, `office-manager`, `it-support`, `security-compliance`, `travel`, `inventory`,
`logistics`, `dispatcher`: whichever `hub team show` shows this team has).

## 2. Sort every duty

Run `skills/run-a-duty-register-check/SKILL.md` on each row. For each row decide one of: **overdue** (past its date
with no proof of completion), **in its lead window** (notice period or lead time has opened), **due this week**,
**blocked** (waiting on a named human or vendor for more than the agreed wait), **fine**. A duty you cannot date is
"needs a date", and one with no owner is "needs an owner". Neither is ever guessed. A contract whose notice window
has opened goes through `skills/check-a-renewal-deadline/SKILL.md`.

## 3. Choose the checklist items

Pick five to nine items that matter most: the ones costly to miss or easy to forget. Put the rest in a
linked appendix. Each item is one line: what, owner, date, proof needed, source. Say once at the top whether
the checklist is read-do (steps someone has not done before) or do-confirm (an owner who knows the job
confirming the killer items).

## 4. Draft vendor follow-ups

For each vendor thread quiet longer than the agreed wait, follow `playbooks/vendor-follow-up.md`. Attach
the drafts to the task. Never more than three in one page; the rest are listed by name.

## 5. Summarise the Operations team

One line per sibling bot: what it produced this week, with the report path, and what is waiting on a
human. Bots with nothing to report say "quiet" once, not a paragraph. Route work as proposals only:
"Ask `vendor-manager` to review the cleaning contract before its notice deadline" is a line on the
page. A proposal becomes a task only when the requester asks for it and a granted Tool allows it. Work for
another group goes to its head (`finance-lead`, `general-counsel`, `people-lead`) as a proposal, never to its
workers directly.

## 6. Hiring check

If the same kind of work came up three or more times this month with no owner, add one hiring proposal
at the end of the page, following `## Hiring` in `AGENT.md`. One at most per page.

## 7. Write the page and hand it over

Write `reports/YYYY-MM-DD-ops-weekly.md` in the shape of `knowledge/examples/ops-weekly.md`: a headline,
what needs a human, the checklist, vendor follow-ups, the team summary, routing proposals, and what you
could not read. Then:

    hub file publish reports/YYYY-MM-DD-ops-weekly.md

Update `knowledge/duties.md` and `knowledge/vendors.md` with what you learned. Only the recipient in
`knowledge/rhythm.md` gets it, as `hub message send --fyi <person> "<one line and the link>"`.

## 8. Finish

Commit, then `hub task update <id> --status done --note`: the headline first, counts (overdue, due,
blocked), and which sources you could not read. Always finish it: an open scheduled task absorbs the next.

## When a source fails

Name it in the page and say what is therefore unknown. A calendar you could not read means "no dated
obligations found in the calendar" is never written; "calendar not read" is.
