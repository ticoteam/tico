# Weekly market briefing

Once a week the Librarian writes one short page on what changed in your market, and the server works out which of your
bets it touches. The Strategy Bot looks only at those.

[Strategy](strategy.md) · [Librarian](librarian.md) · [Market watch](market-watch.md) · [Routines](routines.md)

## What it holds

- A title and a page of markdown, kept as the market page `market/briefings/<week>`.
- Items: one sentence each of what happened, with a kind (`change`, `signal`, `theme` or `size`) and the market entity
  ids it is about.
- The bets it touches. These are worked out from the entity ids, not written by the Librarian: an active bet of the
  published strategy is touched when it cites one of the entities, or its market wrong-if watches one.

There is one briefing a week, named by its ISO week (`2026-W44`). Posting the same week again replaces it and keeps the
first date.

## Built-in automations

The hub makes two built-in automations for the Librarian (see Automations):

- **Market briefing**: daily at 07:00 team time. The Librarian's turn writes the briefing (a one-line title, then a
  bullet per point); the run's `post_update` step, `{"to": ["market", "updates"]}`, posts it as this week's briefing
  and adds one line pointing at it to the Librarian's daily update. A daily briefing replaces the week's title and page; the items the Librarian tagged that week (`hub market briefing post --items-file`), and the bets they touch, stay.
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

On the Market tab of [Strategy](strategy.md), one line under the tabs: the week, the title, the number of items and a
chip for each bet it touches. **Read** opens the page. With no briefing the line is not there.

## What follows

When a briefing is posted the server emits `market.briefing` for that week. The Strategy Bot has a routine on it, **Market
briefing**, which opens a task only when the briefing touches a bet. For each touched bet it asks whether the bet is
still right. Most weeks the answer is yes and nothing more happens. When it is not, the Strategy Bot files one
`strategy_draft` proposal in your Needs you, marked as from the weekly briefing. Publishing stays your click.

A replacement that touches different bets than before opens one more task for them.

A new Strategy Bot starts with this routine. A Strategy Bot that already exists keeps the routines it has: add this one as an
automation on the event `market.briefing` with a Strategy Bot turn that follows `playbooks/market-briefing.md`
([Routines](routines.md)).

## Commands

| Command | Does |
|---|---|
| `hub market briefing post --week <2026-W44> --title <t> --body-file <f> [--items-file <items.json>]` | Post or replace a week. Owner or Librarian |
| `hub market briefing latest` | The newest briefing with its body and the bets it touches |

`--items-file` is a JSON list of `{"text", "kind", "entity_ids"}`. Both are also hub tools
(`hub_market_briefing_post`, `hub_market_briefing_latest`).

## API

| Route | Does |
|---|---|
| `GET /api/v2/market/briefings` | The briefings, newest first, without bodies. Market readers |
| `GET /api/v2/market/briefings/latest` | The newest one, or `null` |
| `GET /api/v2/market/briefings/{week}` | One week |
| `POST /api/v2/market/briefings` | `{week, title, body, items}`. Owner or Librarian |
