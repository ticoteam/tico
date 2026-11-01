# Write the briefing

The built-in Market briefing runs this playbook every day at 07:00 team time, as its own turn. The turn's reply is the
briefing: the run posts it to Market as that week's briefing and adds one line pointing at it to the daily update. A daily
run replaces the week's briefing. One short page, readable in two minutes. Nothing changed since yesterday: the reply is
the one line `Nothing new in the market since yesterday.`, because an empty reply cannot be posted.

## 1. Gather the week

Gather what changed since yesterday:

- `hub market delta --since 1d`: the graph's changes, grouped by entity, each with its source.
- `hub listening item list --destination market`: the items accepted since yesterday, which is what listening heard.
- `hub strategy show`: the published bets, each with its `why` and `wrong_if`. An item about an entity a bet cites
  matters most, and goes first.

## 2. Write the items

The reply is text, so each point is one bullet: one sentence of what happened, its source domain and date in brackets,
and the market entity it is about in square brackets:

    - Example Co moved its Pro plan from 29 to 39 a seat (pricing page, Oct 24). [company/example-co]

Group the bullets by what they touch (pricing, product, people, customers). When a bullet's entity is one an active bet
cites, name the bet in the bullet ("touches the bet on <bet id>"). The reply carries no structured items, so the server
does not read entity ids or bets from it: the words are for the people who read the page.

Write down what happened, never what the team should do about it. The bets are the owner's and the Strategy Bot's to
weigh.

## 3. Write the page and post it

The reply is the page: a one-line title of a few words, then the bullets under their groups. The run posts it. You do
not call a post command for the daily briefing.

A hand post is only for when a person asks in a task. Its items carry the entity ids the server reads, so it is the way
a week's briefing is tied to a bet:

    hub market briefing post --week 2026-W44 --title "<a few words>" --body-file briefing.md --items-file items.json

`--week` is the ISO week of the day you run. The items file is a JSON list of `{"text", "kind", "entity_ids"}`, where
`kind` is `change` (something a competitor did), `signal` (a customer or channel said it), `theme` (a pattern across
several) or `size` (a market number). At most 600 characters an item and 20,000 for the page. The next 07:00 run replaces
the week's briefing, so a hand post holds only until then.

## 4. Finish

Do not message the Strategy Bot about the briefing. The server opens its task only when a briefing's items touch a bet,
and the daily reply carries none.

Done when the run's reply is the page, or the one line when nothing changed, and a hand post was made only if a person
asked for one.
