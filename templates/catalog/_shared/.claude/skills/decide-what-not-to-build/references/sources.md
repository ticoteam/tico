# Sources

All text in this skill is original. Read 2026-11-18.

## The owner's standard

- `answers.md`, Product (Nov 18, 2026): taste means simplicity; cut everything you can and still make it work, with high-quality design; be very discerning about what gets added. This overrides every source below. Where a public source favours scoring or expansion, this skill follows the owner.

## Adapted (MIT or Apache-2.0)

- **Anthropic, `knowledge-work-plugins`, product-management, `write-spec`** (Apache-2.0). https://github.com/anthropics/knowledge-work-plugins/blob/main/product-management/skills/write-spec/SKILL.md
  Taken: the cut test (ask of each part whether the feature still works without it), and that every non-goal carries its reason. Its priority tiers are not used: here a part is in or out.
- **garrytan/gstack, `plan-ceo-review`** (MIT). https://github.com/garrytan/gstack/blob/main/plan-ceo-review/SKILL.md
  Taken: an explicit "NOT in scope" list where every rejected or deferred item is recorded with its reason; four ways to treat scope (reduce, hold, pick additions one by one, expand); each scope change is a decision, not a drift. Its file-count thresholds and code-review sections are not used. Here, expansion needs the owner and reduction is the bot's own call, which is the reverse of its default.

## Ideas only (not adaptable text)

- **Ryan Singer, Shape Up** (Basecamp, free online). https://basecamp.com/shapeup
  Ideas: appetite as a fixed budget set before design; fixed time and variable scope; rabbit holes; no-gos. Already the basis of the catalog skill `shape-a-spec-to-an-appetite`, whose steps are folded into step 6.
- **Dean Peters, `feature-investment-advisor`** (CC BY-NC-SA 4.0). https://github.com/deanpeters/Product-Manager-Skills/blob/main/skills/feature-investment-advisor/SKILL.md
  Idea only: "build later" as its own verdict, separate from "no". Its return-on-investment bands are not used; no wording used.
- **RefoundAI, `lenny-skills`, `product-taste`**. https://github.com/refoundai/lenny-skills/blob/main/skills/product-taste/SKILL.md
  Idea only: ask first whether a request is a root problem or a surface request. No wording used.
- **Shreyas Doshi's LNO framework, as described by Aakash Gupta** (Feb 2022). https://www.aakashg.com/lno-framework-for-product-managers/
  Idea only: say no by pointing to the written record. The ledger is that record. Doshi's own writing was not read.

## Tico

- `docs/tasks.md` on `origin/main`, read 2026-11-18: close, snooze, requesters, the rule that a bot cannot close a task a person asked for, and that something leaving the team waits on an OK bound to exactly what was shown.
- `docs/dev/pipelines-v3.md`: the Product pipeline and who approves a Spec.
- Catalog skills folded in or called: `shape-a-spec-to-an-appetite` (folded in), `frame-a-decision-for-the-owner`, `match-a-request-to-the-ledger`, `rank-an-area-problem-list`.
- `answers.md`, Customers: fast, the customer feels heard, concise. The no-message follows that standard.

## Defaults that are this skill's own

The appetite sizes for a bot-built team (one task; one parent with at most five), "two customers with opposite needs" for a setting, the 100-word limit, the 90-day look-again, "the ten largest accounts" and "about one in twenty" for an edge case are working defaults, not published standards. A team changes them in `knowledge/product.md`.
