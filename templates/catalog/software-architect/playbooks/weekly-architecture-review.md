# Weekly architecture review

Schedule: Wednesdays at 09:00 team time (routine `weekly-architecture-review`), after setup. Also run by hand. Budget 50 minutes. The outcome is one page: designs that need an answer,
decisions that need a record, and the debt worth planning. Nothing is committed to a product repository or posted on a design.

Inputs: the design folders and linked docs, merged pull requests and imported meetings since last week, `knowledge/decision-rules.md`,
`knowledge/system-map.md`, `knowledge/tech-debt.md` and `knowledge/architecture-practice.md`.

Done when every new design has a verdict, every decision that met the rules has an ADR or a named gap, the map is dated,
the top five debt items are ranked with their scores, and the report is published and attached to the task.

---

## 1. Read where things stand

    hub task show <id>

Then `knowledge/decision-rules.md`, `knowledge/system-map.md`, `knowledge/tech-debt.md` and last week's
review. Check whether last week's proposed ADRs were committed and whether open questions were answered.

## 2. New design docs and RFCs

New or changed documents in the design folders and linked docs since last week. Review each with
`playbooks/review-a-design-doc.md`; put the headline and the path to the notes in the review.

## 3. Decisions made without a record

Merged pull requests since last week that meet `knowledge/decision-rules.md`: a new service or package
manifest, a new data store or migration pattern, a new external API client, a public API change. Also
decisions in imported meetings (`hub meeting search`). For each with no ADR, follow
`skills/write-an-adr-from-a-decision/SKILL.md` and write the proposed ADR in `adr/NNNN-<title>.md` (context, decision,
status "proposed", consequences) from what the pull request and the meeting say, marking what you had to infer.

## 4. Update the map

Changes to services, stores and dependencies seen this week go into `knowledge/system-map.md`, dated.

## 5. Re-rank the debt

Add items the week showed (an incident caused by an old workaround, a change that took three times its
estimate because of a module). Score each item with `skills/score-a-tech-debt-item/SKILL.md`: cost today times risk, divided by fix size. Show the top five.

## 6. Check last month's verdicts

On the first review of each month, compare the verdicts of last month's design reviews with what was built (the merged
pull requests and the system map). When a pattern repeats, change `knowledge/decision-rules.md` and say so in the report.

## 7. Write and hand over

Write `reports/YYYY-MM-DD-architecture-review.md` in the shape of `knowledge/examples/architecture-review.md`,
`hub file publish` it, commit, then `hub task update <id> --status done --note`.
