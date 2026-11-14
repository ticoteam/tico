# Usage

Account menu > **Usage** shows what the bots' model runs cost, as an estimate: the tokens each run used. Most models are priced at the
provider's public list price; Terra is priced at Tico's approved rate, not the list price. It is not an invoice.

## Where the numbers come from

The runner adds up the `tokens` events a run's runtime reports (`runner-rs/src/execute.rs`) and sends them with the run's result as
`usage`: uncached input, cached input and output tokens, the model that ran, and how the run was billed. The server keeps them on the run
(`attempts.input_tokens`, `cached_tokens`, `output_tokens`, `usage_model`, `provider`, `est_cost_usd`, `billing`; migration 12, moved
from the old `turns` table by migration 0072) and prices them
from `providers.PRICES`, in USD per million tokens, dated by `providers.PRICES_AS_OF`. A cache write counts as input; batch, priority and
regional premiums are not modelled. A model with no price keeps its tokens and shows a dash for the cost.

Cache writes are counted apart: the cache writes a run's harness reports (Claude's `cache_creation_input_tokens`, Cursor's
`cacheWriteTokens`, pi's `cacheWrite`) go to `cache_write_tokens` (on `attempts` and `usage_facts`) and are left out of
`input_tokens`, which is then the uncached input only. They are still priced as input. A runtime that reports no cache
writes (Codex, Gemini, Antigravity, Muse Code) records 0. A run recorded
before they were counted apart, or by a runner too old to send them, has NULL there and its `input_tokens` include them.

Some models charge more for a request with a long prompt (`providers.LONG_PROMPT`, dated by `LONG_PROMPT_AS_OF`; today Claude Haiku
5.5 over 100,000 tokens, counting cache reads and writes). For those, the Claude runner sends each model request's tokens as `requests`
and the server prices each request at its own rate. A Decide judge call is one request too, so its prompt pays the long tier for Claude Haiku 5.5 (approved 2026-11-06) and gpt-6.1-terra. Tokens no request accounts for (an older runner, another runtime, a run of more
than 2,000 requests) are priced at the lower rate, and the run is counted in `low_estimate_runs`: the page says **estimate, may be
low**. A runtime that reports no
tokens (Grok today) shows runs and no tokens. When every API run lacks reported usage or a model price, the cost shows a dash; a measured zero remains $0.00.

A run on a ChatGPT or Claude sign-in (Codex "Signed in with ChatGPT", Claude with `claude.ai` or a long-lived OAuth token, a Cursor
account) is `billing: subscription`. It costs nothing extra, so its figure is shown as "API-equivalent" and kept out of the estimate of what
was spent.

## A bot's detail

Click a bot on the Usage page to see what explains its spend, under the page's range and filters, from one request
(`GET /api/v2/usage?bot=<slug>`):

- **A summary**: its API-equivalent cost, runs, tokens and cache rate.
- **Tokens**: one bar split into four parts, with a legend giving each part's count and share of all tokens.
  - **Cache reads**: input the provider read from its prompt cache (`cached_tokens`). On most models these cost about a
    tenth of new input.
  - **Cache writes**: input written to the cache for later turns (`cache_write_tokens`; 0 on runtimes that report none).
  - **New input**: input sent fresh, neither read from nor written to the cache (`input_tokens`).
  - **Output**: what the model wrote back (`output_tokens`).
- **Cache rate** = cache reads ÷ (new input + cache reads + cache writes): the share of input tokens read from cache, as a
  whole percent. A dash when no input tokens were reported. A runtime that counts cache reads differently (or not at all)
  reads lower.
- **Tasks**: the bot's top 10 tasks by cost, each with its runs, tokens, cache rate, cost and share of the bot's cost, then
  how many more and what they cost. Runs with no task always keep their own row. Runs are grouped by task title, so a routine's runs add up on one row; a private
  task reads "A private task", one you can't open "A task you can't open", and runs with no task "Chats and other runs".
- **Models**: every model the bot used, with the same columns; a model with no list price shows a dash for cost.
- **Cost per day**, only when the range has two days or more; hover a column for its cost, runs and cache rate.
- **Automations**, and **Export CSV**: the bot's days with their token counts, costs and `cache_rate` (0 to 1, empty
  when no input tokens were reported).

## Who sees it

The owner and the admins see every bot. Anyone else sees the bots they own. A bot's own credentials cannot read it.

## Limits

A limit is a cap in estimated USD per 30 minutes, day, week or month, set at three levels.

- **A bot**: its own limits; the team has a default (**Each bot**) for bots with none of their own. The owner of a bot may set its
  limit, no higher than the team default.
- **A group** (department): the combined spend of the group's bots, and of the bots in groups nested under it.
- **The whole team**: the combined spend of every bot. Set it to keep all the bots together under what a shared subscription or
  budget can take in an hour, so they slow down before the provider stops them for hours.

Usage > **Limits** is one table of every limit: the default and the whole team, then each group and bot with a limit of its own.
**Add limit** adds any group or bot; change several and save them together. A bar under an amount is what that window has spent.
The owner and administrators set the default, group and team limits; a group's lead sets the group's and its bots'. A bot's
Usage row and Settings > Bots open its limit too. None is set until someone sets it.

- At 80% of a limit, one message goes to the bot's owner (else the team owner); for a group or the team, to the team owner. A chip
  shows on the bot's Usage row.
- At a limit, every bot it covers takes no new job until the period turns over or the limit is raised. The job stays queued, the bot
  reads "Paused: over its 30-minute limit" (its own) or "Paused: the team is over its 30-minute limit" on its page and in Tasks ("hourly" for an hourly limit), and a run
  already going finishes. A group's or the team's total is shown only to the owner and administrators.
- What counts is the estimate of the runs the bots finished that started in the UTC half hour, the UTC day, the week from Monday 00:00 UTC, or the UTC month. An hourly limit is saved as the half hour
  at the same rate: half of it, rounded up to $0.10. Runs still going are counted when they end, so a period's total can pass
  its limit by what was running when the hold began. The server logs each hold once per limit and period in each process,
  with how many runs were going across the team. Subscription runs count their API-equivalent unless the team turns off "Count subscription runs" (in
  the default limit), for every level.
- A Finance budget that refuses spend at its limit on AI services is one more limit: at its cutoff every
  bot it covers takes no new job and reads "Paused: AI services budget reached" (no amounts) until the budget is raised or its
  period ends. Limits and budgets both apply; whichever is reached first holds the bot. Only spend billed through the Tico AI
  Gateway counts against a budget: runs on the company's own keys, sign-ins or subscriptions do not. The Video production budget
  pauses no bot (a run need not make images); at its limit new images are refused for whoever it covers. A new team's budgets start with no amount,
  so nothing is cut off until a person sets one; the team's monthly limit here is separate and does not set a budget.
  Its hold log identifies the budget and its period end (or that it must be raised), without amounts.
  At the cutoff Finance also sets the Gateway key's own spending cap, and clears it when the budget is raised. The cap
  follows the key in use: a new, rotated or environment key, or a new Gateway URL, is capped or cleared within a few
  minutes. A key that is no longer in use keeps the cap it had; if it comes back, Finance sets its cap again.
- Separate from these cost limits, each bot has a run budget (runs per hour and per day, default 300 and 2500) that stops a runaway loop; see [Run budgets](how-it-works.md).
- A message is sent once per bot or pool, period and limit, so raising a limit arms it again.

`GET /api/v2/usage/limits`, `PUT /api/v2/usage/limits` (the default), `PUT /api/v2/usage/limits/{bot}` (a field set to null follows the default; one left out stays),
`PUT /api/v2/usage/pools/company` and `PUT /api/v2/usage/pools/groups/{group}` (a field set to null removes it; one left out stays).

Hand-off caps, run budgets and loop guards are separate safety controls. The limits on this page do not replace them.

**Clear automatic queue** (Usage, owner and administrators) cancels queued automatic wakes and waits for the whole team
or one group and its subgroups. Tasks stay in their stage. Messages from people stay queued, including Tico's notices
carrying their messages or reporting their own acts. A run that has already started finishes. `POST
/api/v2/usage/queues/purge` with `{"scope": "company"}` or `{"scope": "group:<id>"}`.

### Strategy > Budget

Strategy > **Budget** (`#/strategy/budget`) shows the same limits as one tree: the company on top, then the groups, people and bots
as on Org, with the helpers in their own branch. Each row shows what it spent over the period picked above the list (**7 days**,
**30 days** or **This month**; a group or person adds up everyone under it), and its limit that binds when one is set, with a thin
bar. **Near limit** keeps the rows that are near or over a limit, or paused, or that have used up the day's count of a task cap they send. Tapping a row opens a panel (a sheet on a phone) with
where the period's spend went, by model and by top task, then the row's limits and task caps. Limits are saved there as they are on
Usage > Limits; the hub decides who may change which, and a row it says is not yours is read only.

## API

`GET /api/v2/usage?from=&to=&department=&bots=&group=bot|day|routine|task|harness|model|effort|subscription` (UTC dates, default the last 7 days, at most 366) returns `rows` and
`totals` with `runs`, `input_tokens`, `cached_tokens`, `cache_write_tokens` (NULL runs sum as 0), `output_tokens`, `est_cost_usd` (null when every API-billed run used an unpriced
model or reported no usage), `subscription_equiv_usd`, `unpriced_runs`, `low_estimate_runs`, and on each row its `share` of the total. `?bot=<slug>` returns that bot's `daily` series and top
`routines`. The legacy `department` query parameter filters by group; `bots` (slugs, comma separated) narrows to those bots; `group` selects how rows are aggregated (`task`: the top 25 tasks by spend, a private one named only as one, one the reader cannot open only as that, runs with no task together). A group lead also sees the bots in the groups they lead, as `/usage/limits` lists them. See `GET /api/v2/openapi.json`.

## Filter by the settings used for a run

Usage can group and filter by harness, model, effort and named subscription, and sort by estimated
cost, tokens, runs or name. Filters also apply to a bot's daily detail and CSV. Names refer to login
profiles, not people or subscription ownership. Assign a subscription in the bot's settings; changing
it affects future runs, not recorded usage.

Updated runners report the primary and fallback portions separately. A run that uses two models can
appear in both groups, while the overall run count counts it once. Token and cost totals include both
portions. API spend and subscription API-equivalent estimates stay separate, including when the
fallback uses a different billing method. Existing spend limits use the same portions.

## What is counted, and what Finance books

Each measured part of a run (the primary model, and a fallback if one ran) is counted once, and recorded counts are never
edited. The usage page and the limits add these up. A run that counted nothing has no count, and its row says why.

Spend billed through the Tico AI Gateway is booked in Finance from the Gateway's meter, the amount the Gateway charges. Finance
reads the meter once a day. The usage page still shows the estimate, which can differ from the meter.

Older runs retain their recorded totals. Unreported harness, effort or subscription values are shown
as **Not recorded / not applicable**, never filled from current bot settings. Automatic model choices
remain the runner's reported choice; this does not discover an underlying model selected by a provider.
A new runner talking to an older server falls back to the older final-portion usage report. Update the
server and runners to get complete primary/fallback attribution. No provider credentials are stored
with usage. This page is an estimate of Tico runs, not a provider invoice or a subscription quota meter.

Why bots spend what they do, what was done to cut it, and the weekly review: [Token efficiency](token-efficiency.md).
