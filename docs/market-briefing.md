# Weekly market briefing

Once a week the Librarian writes one short page on what changed in your market, and the server works out which of your
bets it touches. The Strategy Bot looks only at those.

[Strategy](strategy.md) · [Librarian](librarian.md) · [Market watch](market-watch.md) · [Routines](routines.md)

## What it holds

- A title and a page of markdown, kept as the market page `market/briefings/<week>`.
- Items: one sentence each of what happened, with a kind (`change`, `signal`, `theme` or `size`) and the market entity
  ids it is about.
- The bets it touches. These are worked out from the entity ids, not written by the Librarian: an active bet of the
  published strategy is touched when it cites one of the entities, or its market wrong-if watches one. Each item read
  also lists, as `bets`, the touched bets that watch one of its own entities.
- Answers: one sentence each for the Market page's questions, keyed `changed` (What changed?), `money` (Where is the
  money going?), `players` (Who are we up against?), `reach` (How does it reach buyers?) and `saying` (What are buyers
  saying?). The server keeps at most 140 characters of each, cut at a word. A question with no answer shows no line.

When the Librarian gathers many items, `hub gateway digest` groups them through the Gateway ([AI Gateway](ai-gateway.md)): the
groups order the bullets. The items and the briefing stay the Librarian's.

There is one briefing a week, named by its ISO week (`2026-W44`). Posting the same week again replaces it and keeps the
first date.

Each of the company's segments ([Strategy](strategy.md#segments)) has its own briefing for the same week, kept apart
from the whole market's by `segment_id`: the segment's id, or `''` for the whole market. A segment's page is
`market/briefings/<week>-<segment id with the slash made a dash>`, for example `market/briefings/2026-W44-segment-acme`.
Posting a week and segment again replaces only that segment's. Only the company's own segments take a briefing; any
other id is a 404.

## Built-in automations

The hub makes two built-in automations for the Librarian (see Automations):

- **Market briefing**: daily at 07:00 team time. The Librarian's turn writes the briefing (a one-line title, then a
  bullet per point); the run's `post_update` step, `{"to": ["market", "updates"]}`, posts it as this week's briefing
  and adds one line pointing at it to the Librarian's daily update. A daily briefing replaces the week's title and page; the items the Librarian tagged that week (`hub market briefing post --items-file`), the bets they touch and the answers (`--answers-file`) stay. The same run also posts one briefing per segment with new items, with its `segment_id` (see Segments); a segment with none gets no post, and a segment's briefing opens the Strategy Bot's task only when it touches an active bet.
- **Market listening · <destination>**: one per listening destination the Librarian receives. Each match is a
  Librarian turn; the turn changes the graph through the market routes, never the match itself.

What an automation's turn posts or changes names it: the briefing and the update carry `source: {automation, run,
task}` (each briefing read also says its `cadence`: `daily` when its automation posts more than once a week, so
Market dates it by the day; else `weekly`), and each entity or edge on `GET /api/v2/market/graph` that a turn changed carries `source: {automation, run,
turn: {attempt, bot, task}, match}`, `match` being the listening post that started it. The top of the org may pause
either built-in; neither can be deleted. Each stands down while the team's own Librarian automation does the same
work, and the briefing starts paused while a Librarian briefing routine still runs. Converting the routines turns
that routine into the built-in, on the routine's time and words.

## Where it shows

The Market tab of [Strategy](strategy.md) is one page of five questions. **What changed?** lists the latest briefing's
items, each with its kind and a chip for each bet it touches, its date and **Read**, which opens the whole page. Each
question's answer line comes from the latest briefing's answers. With no briefing, What changed? is not there.

## What follows

When a briefing is posted the server emits `market.briefing` for that week. The Strategy Bot has a routine on it, **Market
briefing**, which opens a task only when the briefing touches a bet. For each touched bet it asks whether the bet is
still right. Most weeks the answer is yes and nothing more happens. When it is not, the Strategy Bot files one
`strategy_draft` proposal in your Needs you, marked as from the weekly briefing. Publishing stays your click.

A replacement that touches different bets than before opens one more task for them.

A new Strategy Bot starts with this routine. A Strategy Bot that already exists keeps the routines it has: add this one as an
automation on the event `market.briefing` with a Strategy Bot turn that follows `playbooks/market-briefing.md`
([Routines](routines.md)).

## Segments

The Market page of a segment is the same one page, scoped to the segment: its newest segment briefing, the sizes the
Librarian filed for it, its players, and the history of it, its sub-segments and its players. The compare view reads
every segment at once. Both read stored rows only, so a number that is not known is `null`, never a guess.

- `money` is the segment's accepted size in USD, for the current year, the offering dimension and the product basis. An
  estimate is not money until it is accepted, so an unaccepted one shows as `null`.
- `yearly_spend` is that money, or where the segment has no accepted size, the sum of its sub-segments' yearly spend,
  counting accepted sizes only; `null` when neither has one.
- `growth` is the accepted size's `growth_pct`, a percent a year.
- `our_share` is the company's newest revenue or ARR figure for the segment over `yearly_spend`, as a percent to one
  decimal. Only a figure whose `as_of` is in the spend year counts: one from another year leaves it `null`.
- `briefing_week` is the week of the segment's newest briefing.
- `changed` is the newest briefing's items of the whole market (`segment_id` `''`) and of each segment, taken round-robin
  so none crowds out the rest: at most 30 in all, newest week first. Each item carries its `segment_id` and its `week`.
  The active bets are read once for all of them.

A segment's briefing emits `market.briefing` with its `segment_id` (payload `{week, bets, segment_id}`) only when it touches
an active bet, which is when the Strategy Bot's task opens for it. A segment with none is posted and shown, and tells the
Strategy Bot nothing. The whole market's payload stays `{week, bets}`.

"Ask Librarian to research" on a segment's page files a task titled `Market for <segment name>`: the Librarian answers
the five questions for that segment, posts its briefing with `--segment`, and files its sizes as proposed, which the
owner accepts. While that task is open, asking again returns it. Its note is what the person asked. A person may ask. A bot may ask only when the bot-contact rules let it open a task with the Librarian (a refused bot gets
403 `bot_contact` and no task). The Librarian must be running (409 otherwise). The Librarian's own rules are in [The Librarian](librarian.md#segments).

## Commands

| Command | Does |
|---|---|
| `hub market briefing post --week <2026-W44> --title <t> --body-file <f> [--items-file <items.json>] [--answers-file <answers.json>] [--segment <segment id>]` | Post or replace a week, or that segment's week. Owner or Librarian |
| `hub market briefing latest [--segment <segment id>]` | The newest briefing (the segment's, with `--segment`) with its body and the bets it touches |
| `hub market segments` | The company's segments side by side: money, yearly spend, growth, our share, the newest briefing week, and the newest briefing items across them. Market readers |

`--items-file` is a JSON list of `{"text", "kind", "entity_ids"}`; `--answers-file` a JSON object of the answers, by
key. Both commands are also hub tools
(`hub_market_briefing_post`, `hub_market_briefing_latest`). `hub market segments` (`hub_market_segments`) reads the compare
view. The segment page and the research request have no hub command yet.

## API

| Route | Does |
|---|---|
| `GET /api/v2/market/briefings?segment=` | The briefings, newest first, without bodies. `segment` is a segment's id; left out, the whole market's. Market readers |
| `GET /api/v2/market/briefings/latest?segment=` | The newest one, or `null`. `segment` as above |
| `GET /api/v2/market/briefings/{week}?segment=` | One week |
| `POST /api/v2/market/briefings` | `{week, title, body, items, answers?, segment_id?}`. Without `answers` the week's stay; `segment_id` left out is the whole market's. Owner or Librarian |
| `GET /api/v2/market/segments/compare` | `{segments: [{id, name, color, money, yearly_spend, growth, our_share, briefing_week}], changed: [{text, kind, entity_ids, bets, segment_id, week}]}`: every segment, and the newest briefing items across them, round-robin over the whole market and each segment (at most 30; `segment_id` `''` is the whole market). `color` is the segment's color or `null`. Market readers |
| `GET /api/v2/market/segments/{segment_id}/page` | `{segment: {id, name, who, color, rank}, our_share, answers, briefing, sizes, players, what_changed}`. `our_share` is the figure compare shows for the segment, to the same rule (the spend year, the roll-up); `answers` are the newest segment briefing's five answers; `sizes` is `{year, dimension, basis, chosen, rolled_usd, estimates, disagree, bases}`; `what_changed` the history rows (at most 30). The id is the segment's, with its slash: `/api/v2/market/segments/segment/acme/page`. 404 for a segment that is not the company's |
| `POST /api/v2/market/segments/{segment_id}/research` | `{note?}` → `{task_id, created}`. Asks the Librarian for the segment's five answers and sizes; `created: false` when its task is already open. A person, or a bot the bot-contact rules admit (403 `bot_contact` otherwise). 404 for a segment that is not the company's; 409 when the Librarian is not running |
