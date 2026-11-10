# Weekly growth summary

Schedule: Fridays at 14:00 team time (routine `weekly-growth-summary`), after setup. Also run by hand on request. Budget 40 minutes. The outcome is one page for the owner: how the week went across marketing and the pipeline, what is blocked, which deals need a human, the next six weeks on the calendar and proposed priorities. Nothing is shared, sent or changed, and no task is created.

Inputs: `knowledge/workstreams.md`, `knowledge/pipeline-rules.md`, `knowledge/team.md`, `knowledge/calendar.md`, `knowledge/priorities.md`, last week's page in `reports/`, and each owner's tasks, updates and published reports since last Friday.
Done when: `reports/YYYY-MM-DD-growth-week.md` is written and published; every workstream has a line (or "no report"); the pipeline section names its source and date; the calendar covers six weeks; the task note gives the headline, the path and any source that could not be read.

---

## 1. Read where things stand

    hub task show <id>

Then the knowledge files above and last week's page. If last week flagged an action, check whether it happened and say so.

## 2. Gather the week, by workstream

For each workstream in `knowledge/workstreams.md`, read what its owner produced since last Friday: `hub task list`, `hub update list --kind weekly --bot <slug>`, the owner's newest published report, and imported meetings that mention it. A bot or human with nothing this week is "no report", never "on track".

## 3. Read the pipeline

Use the source `knowledge/pipeline-rules.md` names: a read of the CRM (only ever a read), the sales roll-up's latest report, or the pipeline notes. For each open deal take its stage, amount, days since last activity and next step. Compare with last week's page to find what entered, moved, closed and slipped. List tasks waiting more than five days on a human or on a missing input.

## 4. Give each workstream one line

Status (red, amber, green, or no report), what moved since last week, the evidence with its date, the owner and the next step. Red means blocked or missed; amber means at risk with a named reason.

## 5. Choose the priority deals

Three to five, in this order: closest to close and needing a step; stalled past the threshold (default 14 days); large deals over the "always show" size; new deals with no next step. Skip healthy ones. For each stalled one, write one coaching line for its owner (`skills/write-a-coaching-note-on-a-stalled-deal/SKILL.md`).

## 6. Forecast

From the sales roll-up: commit, best case and pipeline for the period, and the deals it flagged. Put the gap to the period's target in one line, if `hub goal list` shows one. At most five deals to inspect on the forecast call. Use `skills/build-a-forecast-call-pack/SKILL.md`.

## 7. Blockers and asks

List each blocker once: what is blocked, who can clear it, what they must decide, by when. Waiting on missing facts or Tools are named as blockers.

## 8. Calendar, six weeks ahead

Update `knowledge/calendar.md` from `hub calendar list` and the workstreams. Flag collisions (two sends or two launches the same day), launches inside two weeks with no brief or owner, demos without an owner, and gaps.

## 9. Propose next week's priorities

At most three, each with the workstream, the reason and the owner. Say what was dropped and why. Routing and reassigning are not part of this playbook; a request for them goes through `playbooks/route-a-request.md`.

## 10. Routing and hiring

For each new lead, account or request with no owner, propose the owner in one line with the reason, following `playbooks/route-a-request.md`. If the same unowned work came again this week, note it under Hiring and follow `playbooks/propose-a-hire.md`.

## 11. Write the page and hand it over

Write `reports/YYYY-MM-DD-growth-week.md` in the shape of `knowledge/examples/growth-week.md`, then `hub file publish reports/YYYY-MM-DD-growth-week.md`. Commit, then `hub task done <id> -m "..." --version N`: the headline, the path, what you could not read. Always finish it: an open scheduled task absorbs the next.

## When a source fails

Name the source and what is therefore unknown in the closing line, and keep going with the rest. A page built from two workstreams out of five that reads like the whole picture is worse than none.
