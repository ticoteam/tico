---
name: propose-a-hire
description: Propose one engineering bot from the team templates for recurring work that has no owner, with counts, dates and links as evidence, the template's first routine, the roster checked first, and a 60-day wait before a refused proposal is raised again. Carry out a hire only when the task asks for it and a Tool allows it.
---
# Propose a hire

When to use: when recurring engineering work has no owner and a bot from `team_templates` in `card.yaml` could take it.

Steps:
1. Check the roster first: `hub template list` and `hub team show`. Never propose a bot that exists or is already on the team.
2. Name the recurring work with its evidence: the count, the dates and the links. One occurrence is not recurring.
   Starting threshold: three occurrences in four weeks. That is a team choice, not a benchmark.
3. Confirm that no one owns the work in `knowledge/areas.md` and that no existing bot's report covers it.
4. Pick the one template from `team_templates` that fits. Name its first routine from its card (title and schedule).
5. Write the five-line proposal on the task or in the summary: the template and its title; the recurring work with
   evidence; how often it happens; the template's first routine; and that it reports to you.
6. Propose on the task. Carry out the hire only when the task asks for it and a Tool allows it:
   `hub task create --owner botops --title "Set up <template> from the templates" --body "<why, the first routine, reports to engineering-lead>"`.
   BotOps builds it. You do not build it.
7. Record the proposal and the answer in `memory/decisions.md`. A no is not raised again for 60 days unless the evidence doubles.

Checks before you finish:
- The roster was checked, and the template is in `team_templates`.
- Each piece of evidence has a count or date and a link.
- The first routine is named as written on the template's card.
- A refused proposal is dated, so the 60-day rule can be applied.

Example (invented, for illustration): Dependency alerts opened 11 times in one repository since 2026-09-01, and seven are older than 14 days. No owner is listed in `knowledge/areas.md`. Proposal: the security engineer template, first routine "Weekly dependency and advisory report" on Mondays at 08:00, reports to engineering-lead. Proposed on the task; not set up, because the task did not ask for it.
