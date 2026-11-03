# Propose a hire

Triggered when the weekly summary finds recurring product work with no owner, or when the owner asks
"do we need someone for this?". Budget 15 minutes. The outcome is one proposal on a task for the owner,
A BotOps task follows only when a task asks for it and a granted Tool allows it.

---

## 1. Prove it recurs

Name the work and count it: at least three occurrences in four weeks, or a weekly job a human is doing
by hand. Link each occurrence (task ids, reports). One-off work is routed, not hired for.

## 2. Pick the template

    hub template list
    hub team show

Choose one template from `team_templates` on this bot's card whose first routine covers the work:
specs and launch checklists → `product-manager`; usage and experiment questions → `product-analyst`;
feature-request ledger, betas, release calendar → `product-ops`; interface copy → `ux-writer`; prices
and packaging → `pricing`; feedback themes → `feedback-analyst`; interviews and studies →
`product-researcher`. If `hub team show` shows it already exists, route to it instead.

## 3. Propose

`hub task create --owner <owner> --title "Hire proposal: <template>"` with five lines: the recurring work
and its evidence, the template, its first routine (title and cadence from its card), that it reports to
you, and what it needs connected. Stop there.

## 4. Carry out the requested hire

    hub task create --owner botops --title "Set up <template>" --body "<why, first routine, reports to product-lead, what to connect>" --parent <id>

Record the hire in `knowledge/team.md` and `memory/decisions.md`. On a no, record the reason so the same
proposal is not made again without new evidence.
