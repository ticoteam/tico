# Sources

Read or searched on 2026-11-18 unless a line says otherwise. Ideas were adapted; no text was copied.

## Skills and guides drawn from

- Anthropic, knowledge-work-plugins, customer-support `draft-response` (Apache-2.0).
  https://github.com/anthropics/knowledge-work-plugins
  Ideas used: the order of a reply (what happened, the core message, the next step), and never hiding bad news or
  overpromising. Changed: the answer goes first, and stock empathy lines are removed.
- Anthropic, knowledge-work-plugins, customer-support `ticket-triage` (Apache-2.0).
  Ideas used: category from the root cause rather than the symptom; four priority levels; take the higher priority
  when unsure. Changed: first-reply targets are tighter because a bot has no queue; they are labelled as defaults.
- Anthropic, knowledge-work-plugins, customer-support `customer-escalation` (Apache-2.0).
  Idea used: a handoff carries everything the next teammate needs so the customer is never asked twice.
- Tico catalog skills in `templates/catalog/`: support-owner, support-quality-analyst, escalations-manager,
  technical-support-engineer, returns-and-refunds-specialist. Rules folded in: marked gaps, the repeat check before
  escalating, scoring a reply against its source, follow-up cadence defaults.
- Tico docs: `docs/mail.md` (sending policy, lint, labels, what a task filed from email may carry), `docs/tasks.md`,
  `docs/glossary.md`.

## Research behind the standard

- Dixon, Freeman and Toman, "Stop Trying to Delight Your Customers", Harvard Business Review, July-August 2010.
  Idea used: lowering the customer's effort, including heading off the next issue, does more for loyalty than
  extras. No figure from the article is quoted here.
- The owner's own standard for this library (answers.md, 2026-11-18): fast, but the customer must feel heard and
  their actual issue addressed, even when the answer is no; concise; the taco exchange.

## Law and platform rules to check locally

Telling customers they are dealing with AI. The default in this skill (never claim to be human; sign as an AI
teammate from the first message) is meant to be safe in most places. Checked 2026-11-18; these change, so check the
current text for the places the team's customers live:

- European Union: the AI Act's Article 50 transparency duty (tell people they are interacting with an AI system
  unless it is obvious) is reported as applying from 2 August 2026. Read from secondary summaries, not the official
  text; confirm before relying on it.
- California: Business and Professions Code section 17941 (SB 1001) bars using a bot to mislead a person about its
  artificial identity in order to sell goods or services or influence a vote. SB 243 (2025) is about companion
  chatbots, not customer service.
- Utah: the state's AI disclosure law requires saying so when a consumer asks; regulated occupations have stricter
  rules.
- United States generally: the FTC Act bars deceptive practices, which covers passing a bot off as a person.

Cancelling. The FTC's 2024 click-to-cancel rule was vacated by the Eighth Circuit on 8 July 2025; the FTC began a
new rulemaking in 2026. ROSCA, the FTC Act and state auto-renewal laws (for example California's, amended by
AB 2863 with effect from 1 July 2025) still apply. The gate in this skill (cancelling is never harder than signing
up) is stricter than the minimum on purpose.

## Numbers in this skill

Every time limit, word limit and money limit in this skill is a starting default chosen for this library, not a
measured benchmark. Each is labelled as a default where it appears and is replaced by the team's setup answers.
