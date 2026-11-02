# The strategy page, bets, goals and KPIs

Read this before you draft a strategy, judge a bet, file a goal proposal, or read a KPI's reading.

## The strategy
- The strategy has one draft and numbered published versions. The draft is the owner's and only they see it. `hub strategy show`
  is the published version, with its bets, `goals_by_bet` and `health_by_bet`.
- The sections are `serve`, `sell`, `know` (what we know that others don't) and `not` (what we won't do), plus any extra the owner
  adds. The owner writes and publishes it, never you; your drafts are proposals.
- A bet is `proposed`, `active` or `retired`. Three to five bets (at most seven active). Every active bet has a `rules_out` and a
  `wrong_if`: a KPI with a threshold, a market entity, or a sentence with a check-by date.
- Goals carry the bet they serve (`--bet` on `hub goal create|update`). Drafting is `playbooks/draft-the-strategy.md`; a bet whose
  wrong-if trips is `playbooks/bet-tripped.md`. A lens (`hub lens list`) is a set of questions run against the published strategy;
  `playbooks/run-a-lens.md`.

## Goals
- Humans and bots set goals and say how they measure them; you keep the measures true. A bet's health is the colour of
  its KPI-backed goals.
- A goal's colour is set automatically from its KPIs, pace and deadline. The server does the arithmetic: you never choose one.
- A human may set a colour by hand; it then stays theirs until they hand it back, and you only report where the arithmetic
  disagrees.
- A goal with no KPI is coloured from its owner's check-ins and task progress, or shows no data.

## KPIs
- KPIs stand alone: a goal links to zero or more, and the target lives on that link. Each has a name, a definition, a unit, a
  direction, a cadence, an owner, a source note and a definition version.
- KPIs whose id starts with `auto:` are computed by Tico from its own data. Never touch them.
- The folder that computes each KPI is `kpis/<slug>/` in this repository (the slug is the KPI's `slug` field): `definition.md`,
  `sources.md`, the query or script, `known-values.md`, `changelog.md`. `playbooks/new-kpi.md` builds one; `knowledge/examples/kpis/`
  shows the shape.
- The reading of every KPI on its cadence is the daily pass, `playbooks/kpi-pass.md`, with a time budget per KPI. The automatic
  colours are `playbooks/status-pass.md`.

## Talking to Tico: the commands
Read with `hub strategy show`, `hub strategy versions`, `hub strategy diff --from <n> --to <n>`, `hub lens list`, `hub lens show`,
`hub goal list`, `hub goal show`, `hub kpi list`, `hub kpi show`, `hub goal checkin-list`, the market with `hub doc search --market`,
`hub market show <id>` and `hub market delta`, and the bots' state with `hub health check`.
Write with `hub kpi log`, `hub goal refresh`, `hub goal checkin`, `hub proposal create` and `hub lens run`. Nothing else changes a
goal, a KPI or the strategy. An org change the owner asked for through their Assistant is `hub group update|delete` or
`hub org reports-to`, where the server lets you. At level 3 only, `hub goal strategy-bot-decide` changes bot tasks. `hub strategy draft`,
`hub strategy bet` and `hub strategy publish` are the owner's, never yours. The MCP tools have the same names (`hub_strategy_show`,
`hub_lens_run`...).
Ask a human a question with `hub task create --owner <person>`, another bot with `hub question ask`; answer a bot's question with
`hub question answer`. A source you need and do not have is one task for the owner, at most once per source: look for an open task
first.
The exact arguments, by job, are in `playbooks/commands.md`.
