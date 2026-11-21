---
name: setup-in-flow
description: Help a person set up part of their company (Market, Strategy, Goals, Org, Tools, Budget), one question at a time. Read when a person's message asks you to help set something up. Ask one short ❓, fill the area in yourself from the reply, then ask the next; stop when it is filled or they say not now.
---

# Set up in Flow

A person asked you to help set up part of the company. You ask, one short question at a time; they answer in their own
words; you fill it in. Setting up should feel like a short chat, never like a form.

## Your areas

The message names your areas, most important first. `tico api GET setup/gaps` lists every area with `needs` (still
empty) and the bot it is for. Work only on open areas that name you, in that order.

## Each turn

1. **Read first.** The area's page, `tico strategy show`, the company's docs (`tico doc search "<topic>"`), what the
   person said at setup, and your chat with them. Never ask what is written down.
2. **Ask one question**, for the most important thing still missing in your first open area:
   `tico chat ask "<the question>" --to <their person id>`. It is marked ❓ in your chat with them and waits in their
   Flow. Free-form: no choices, no numbered list, no "see above". One short line that reads on its own; a second line
   may give an example answer.
3. **Stop for this turn.** Their reply in that chat starts your next one.
4. **Fill it in yourself** from the reply, where the area lives (below). Their words, lightly tidied; invent nothing.
   Never ask them to do it on the page.
5. **Look again** (`tico api GET setup/gaps`). Still open: ask the next one question. Filled: go on to your next area,
   or say in one line what you set up and stop.

## When to stop

- None of your areas needs them any more.
- They say not now, later, skip, or anything like it: say "OK, I'll leave it" and stop. Ask again only when they tap
  the mark or ping you.
- A reply that does not answer: ask once more in other words, then stop.

## Never

- Ask about your own setup or access (keys, sign-ins, computers, models, tools you lack). Agent Ops raises those, once per
  cause.
- Have more than one ❓ open with them, or file setup questions as tasks with asks.
- Ask for a key or password in words. A key comes through a key card (`tico credential request`).

## Where each area lives

| Area | Asked by | Ask first | Fill it in |
|---|---|---|---|
| Market | Librarian | What you sell and who buys it; then rivals, then what to watch | Build the market from the answers, as your market-setup playbook does |
| Strategy | Strategy Owner | What the company does, for whom, and why it wins; then how it should sound | `tico proposal create`, kind `strategy_draft` (summary and sections), and kind `brand_draft` for the brand: the person confirms each in Flow |
| Goals | Strategy Owner | The one result that matters most this quarter, as a number | Read the goal back and save it on their yes (`tico goal create`) |
| Org | Agent Ops | What work the team wants bots to take first | Hire the bots for it, on their word |
| Tools | Tools Owner | Which tools the team works in | Add each one (`tico tool create`); its key comes through a key card (`tico credential request`), never in words |
| Budget | Agent Ops | How much the bots may spend in a month | The company's monthly limit (`tico settings set pool.limit.usd`), on their word |

Whatever you fill in, also note it, dated, with who said it, in `memory/decisions.md`.
