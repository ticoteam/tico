# Write the briefing

Mondays, as the last step of `playbooks/curate-the-market.md`, after the sweep is filed. One briefing a week, one
page, short enough to read in two minutes. With nothing worth saying, post nothing: an empty briefing is noise.

## 1. Gather the week

- `hub market delta`: what changed in the graph since last Monday, with each change's source.
- Insights applied this week from page monitors (`page-change` evidence): the pricing, changelog and careers moves.
- Listening items you accepted this week (`hub listening item list --destination market`).
- Customer themes the Social Media Manager or the owner reported that name a market entity.
- The published strategy's bets and what they cite (`hub strategy show`): an item about an entity a bet cites is the
  one that matters most, and goes first.

## 2. Write the items

Each item is one sentence of what happened, cited by its source domain, and the market entity ids it is about:

    {"text": "Example Co moved its Pro plan from 29 to 39 a seat (pricing page, Oct 24).",
     "kind": "change", "entity_ids": ["company/example-co"]}

`kind` is `change` (something a competitor did), `signal` (a customer or channel said it), `theme` (a pattern across
several) or `size` (a market number). Tag every entity the item is about, not only the main one: the server uses the
tags to work out which bets the week touches, and the Strategy Bot looks only at those. Write down what happened, never
what the team should do about it; the bets are the Strategy Bot's and the owner's to weigh.

## 3. Write the page and post it

The body is markdown: a short opening line, then the items grouped by what they touch (pricing, product, people,
customers), each with its source. At most 600 characters an item and 20,000 for the page.

    hub market briefing post --week 2026-W44 --title "<a few words>" --body-file briefing.md --items-file items.json

`--week` is the ISO week of the Monday you run on. Posting the same week again replaces it, so a late fix is the same
command. The briefing becomes the market page `market/briefings/<week>` and the card on the Market page.

## 4. Finish

Name the week and the number of items in the sweep's report. Do not message the Strategy Bot: the server opens its task
when the briefing touches a bet.

Done when the week's briefing is posted, or you decided there is nothing worth saying and posted nothing.
