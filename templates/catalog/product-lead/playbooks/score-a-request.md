# Score a request

Triggered by a task that asks "should we build X?", and used in the weekly summary for new candidates.
Budget 15 minutes per request. The outcome is one scored proposal with its evidence and a route. Nothing
is added to the roadmap.

Inputs: the request task, `knowledge/scoring.md`, the feedback, support, usage and research reports named in step 2, and `hub goal list --all`.

Done when: the proposal shows all four scores with their sources, names its weakest input, has a route, and any missing fact has been asked for once.

---

## 1. Restate the problem

    hub task show <id>

Write the request as the problem behind it: who, trying to do what, blocked how. A feature named in the
request is one possible answer, not the problem. If you cannot state the problem, ask the requester once.

## 2. Gather the evidence

- Customer Insights Analyst: is it a theme, how many items, which severity (`feedback-analyst` reports).
- Product Operations Manager: how many accounts asked for it (`product-ops` ledger).
- UX Researcher: any snapshots or opportunities that match (`product-researcher` digest).
- Product Analyst: how many users touch the area today (ask with a task if no report covers it).
- `hub goal list --all`: which goal it would move, if any.

## 3. Score

Use `knowledge/scoring.md`. Reach: people or accounts affected per quarter, from a named source. Impact:
the agreed scale (3 massive, 2 high, 1 medium, 0.5 low, 0.25 minimal). Confidence: 100, 80 or 50
percent, by how much of the above is measured rather than guessed. Effort: person-weeks from the human
who owns the estimate; ask on the task if you do not have it, and mark the score provisional.
Score = reach × impact × confidence ÷ effort. Show all four numbers and every source.

## 4. Propose and route

On the task, in five lines: the problem, the score and its weakest input, the goal it serves (or "no
goal: park"), a recommendation (build, research first, park) and the owner of the next step:
`product-researcher` to raise confidence, `product-manager` for a spec. Ask only for missing facts with `hub task ask <id>`, as in `skills/ask-with-recommended-answers/SKILL.md`. Route requested next steps with `hub task create --owner <slug> --parent <id>` and log it in
`knowledge/decision-log.md`.
