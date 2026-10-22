# Usage

Account menu > **Usage** shows what the bots' model runs cost, as an estimate: the tokens each run used, priced at the provider's public
list price. It is not an invoice.

## Where the numbers come from

The runner adds up the `tokens` events a run's runtime reports (`runner-rs/src/execute.rs`) and sends them with the run's result as
`usage`: uncached input, cached input and output tokens, the model that ran, and how the run was billed. The server keeps them on the run
(`turns.input_tokens`, `cached_tokens`, `output_tokens`, `model`, `provider`, `est_cost_usd`, `billing`; migration 12) and prices them
from `providers.PRICES`, in USD per million tokens, dated by `providers.PRICES_AS_OF`. A cache write counts as input; batch, priority and
regional premiums are not modelled. A model with no price keeps its tokens and shows a dash for the cost.

Some models charge more for a request with a long prompt (`providers.LONG_PROMPT`, dated by `LONG_PROMPT_AS_OF`; today Claude Haiku
5.5 over 100,000 tokens, counting cache reads and writes). For those, the Claude runner sends each model request's tokens as `requests`
and the server prices each request at its own rate. Tokens no request accounts for (an older runner, another runtime, a run of more
than 2,000 requests) are priced at the lower rate, and the run is counted in `low_estimate_runs`: the page says **estimate, may be
low**. A runtime that reports no
tokens (Grok today) shows runs and no tokens.

A run on a ChatGPT or Claude sign-in (Codex "Signed in with ChatGPT", Claude with `claude.ai` or a long-lived OAuth token, a Cursor
account) is `billing: subscription`. It costs nothing extra, so its figure is shown as "API-equivalent" and kept out of the estimate of what
was spent.

## Who sees it

The owner and the admins see every bot. Anyone else sees the bots they own. A bot's own credentials cannot read it.

## Limits

A limit is a cap in estimated USD per hour, day, week or month, set at three levels:

- **A bot**: its own limits; the team has a default for bots with none of their own. Change a bot's on its Usage row or in
  Settings > Bots; the owner and administrators also set the default (Usage > **Default limit**). The owner of a bot may set its
  limit, no higher than the team default.
- **A group** (department): the combined spend of the group's bots, and of the bots in groups nested under it.
- **The whole team**: the combined spend of every bot. Set it to keep all the bots together under what a shared subscription or
  budget can take in an hour, so they slow down before the provider stops them for hours.

The owner and administrators set group and team limits (Usage > **Team and group limits**). None of them is set until someone sets
it.

- At 80% of a limit, one message goes to the bot's owner (else the team owner); for a group or the team, to the team owner. A chip
  shows on the bot's Usage row.
- At a limit, every bot it covers takes no new job until the period turns over or the limit is raised. The job stays queued, the bot
  reads "Paused: over its hourly limit" (its own) or "Paused: the team is over its hourly limit" on its page and in Tasks, and a run
  already going finishes. A group's or the team's total is shown only to the owner and administrators.
- What counts is the estimate of the runs the bots finished that started in the UTC clock hour, the UTC day, the week from Monday
  00:00 UTC, or the UTC month. Subscription runs count their API-equivalent only if the team turns on "Count subscription runs" (in
  the default limit), for every level.
- A message is sent once per bot or pool, period and limit, so raising a limit arms it again.

`GET /api/v2/usage/limits`, `PUT /api/v2/usage/limits` (the default), `PUT /api/v2/usage/limits/{bot}` (empty follows the default),
`PUT /api/v2/usage/pools/company` and `PUT /api/v2/usage/pools/groups/{group}` (empty removes it).

## API

`GET /api/v2/usage?from=&to=&department=&group=bot|day|routine` (UTC dates, default the last 7 days, at most 366) returns `rows` and
`totals` with `runs`, `input_tokens`, `cached_tokens`, `output_tokens`, `est_cost_usd` (null when every API-billed run used an unpriced
model), `subscription_equiv_usd`, `unpriced_runs`, `low_estimate_runs`, and on each row its `share` of the total. `?bot=<slug>` returns that bot's `daily` series and top
`routines`. The legacy `department` query parameter filters by group; `group` selects how rows are aggregated. See `docs/openapi/v2.json`.

## Filter by the settings used for a run

Usage can group and filter by harness, model, effort and named subscription, and sort by estimated
cost, tokens, runs or name. Filters also apply to a bot's daily detail and CSV. Names refer to login
profiles, not people or subscription ownership. Assign a subscription in the bot's settings; changing
it affects future runs, not recorded usage.

Updated runners report the primary and fallback portions separately. A run that uses two models can
appear in both groups, while the overall run count counts it once. Token and cost totals include both
portions. API spend and subscription API-equivalent estimates stay separate, including when the
fallback uses a different billing method. Existing spend limits use the same portions.

Older runs retain their recorded totals. Unreported harness, effort or subscription values are shown
as **Not recorded / not applicable**, never filled from current bot settings. Automatic model choices
remain the runner's reported choice; this does not discover an underlying model selected by a provider.
A new runner talking to an older server falls back to the older final-portion usage report. Update the
server and runners to get complete primary/fallback attribution. No provider credentials are stored
with usage. This page is an estimate of Tico runs, not a provider invoice or a subscription quota meter.
