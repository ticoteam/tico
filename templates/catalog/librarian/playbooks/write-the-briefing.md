# Write the briefing

The built-in Market briefing runs this playbook every day at 07:00 team time, as its own turn. The turn's reply is the
briefing: the run posts it to Market as that week's briefing and adds one line pointing at it to the daily update. A daily
run replaces the week's briefing. One short page, readable in two minutes. Nothing changed since yesterday: the reply is
the one line `Nothing new in the market since yesterday.`, because an empty reply cannot be posted.

## 1. Gather the week

Gather what changed since yesterday:

- `tico market delta --since 1d`: the graph's changes, grouped by entity, each with its source.
  What Listening sent you is in there too, once you filed it as an insight and applied it.
- `tico strategy show`: the published bets, each with its `why` and `wrong_if`. An item about an entity a bet cites
  matters most, and goes first.

## 2. Write the items

The reply is text, so each point is one bullet: one sentence of what happened, its source domain and date in brackets,
and the market entity it is about in square brackets:

    - Example Co moved its Pro plan from 29 to 39 a seat (pricing page, Oct 24). [organization/example-co]

When the items you gathered number more than about 20, `tico gateway digest --items-file <file>` (the `tico_gateway_digest` tool)
groups them: order the bullets by its groups and leave out its `dropped` ids; each bullet still names its entity.

Group the bullets by what they touch (pricing, product, people, customers). When a bullet's entity is one an active bet
cites, name the bet in the bullet ("touches the bet on <bet id>").

Write down what happened, never what the team should do about it. The bets are the owner's and the Strategy Owner's to
weigh.

## 3. Tag the items and answer the questions, then reply with the page

The server works out which bets a briefing touches only from tagged items, never from the words, and the Market page's
answer lines come only from the answers file. So post both before you reply:

    tico market briefing post --week 2026-W44 --title "<a few words>" --body-file briefing.md --items-file items.json \
      --answers-file answers.json

`--week` is the ISO week of the day you run. `briefing.md` is the page you are about to reply with. The items file is a
JSON list of `{"text", "kind", "entity_ids"}`, one per bullet: `text` the bullet's sentence, `kind` `change` (something a
competitor did), `signal` (a customer or channel said it), `theme` (a pattern across several) or `size` (a market number),
and `entity_ids` every entity it is about, not only the main one. At most 600 characters an item and 20,000 for the page.

The answers file holds one plain sentence for each question on the Market page, as the graph, the sizes and this
week's items show it today. 140 characters at most each; leave a key out when you have no answer, never write a
placeholder:

    {"changed": "Northwind bundles checks into CI; buyers ask for one bill.",
     "money": "The low end is biggest and shrinking; storefront and scenes grow fastest.",
     "players": "Northwind owns the low end; Initech is moving into ours.",
     "reach": "Marketplaces for homes; installers for towns and shops.",
     "saying": "Talk doubled this week; most of it is about price."}

`changed` is what changed this week, `money` where the market's spend is and where it grows, `players` who we are
up against, `reach` how the market reaches its buyers, and `saying` what buyers say in listening. With nothing new
since yesterday, post no answers file: the week's answers stay.

Then reply with the page: a one-line title of a few words, then the bullets under their groups. The run posts your reply
as the week's briefing and keeps the items you tagged. A person who asks for a briefing in a task gets the same post.

## 4. Finish

Do not message the Strategy Owner about the briefing: the server opens its task when the items touch a bet.

Done when: the reply is the page (or the one line when nothing changed), every bullet about a market entity was
posted as a tagged item first, and the answers were posted with them.
