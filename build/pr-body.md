A person can hide the CEO Bot from their own Team chart and bring it back; nothing about the bot changes. Nothing is open beyond review.

Parent: Tico task 425bba7f ("Team chart: let a person hide the CEO Bot from their own chart view"), Dev ticket #679.

## What it does
- **Hide from my chart** in the CEO Bot's row menu on the Team sidebar (the `…` button, labelled "More for <name>"; Move up/down stay in the same menu when the row has them). A toast confirms it, by name, with a **Show on my chart** button.
- **Settings > Bots**: the CEO Bot's row says "Hidden from your chart · Show on my chart" (or offers Hide from my chart), and its editor has the same line. Both save at once, separately from Save bot. The row works for anyone who can see the list; the editor only for people who can manage the bot.
- Every label uses the bot's current display name (`ceoBotName()`), so it works after a rename.

## Storage
The existing per-person store: the **`preferences` table** (actor, key), key **`chart.hide_ceo_bot`**, a boolean, written through `POST /api/v2/preferences/chart.hide_ceo_bot`. That route now refuses a non-boolean (422) and a bot caller (403) for this key. Nothing is written to the bot's config, `reports_to`, access or level.

## Server
`ceo_bot.hidden_for(c, who)` reads the caller's own row (people only; a bot's or token's view is never hidden). It is passed as `hidden=` to `chart_entries` and `chart_groups`, which then return the stored entries and groups as they are, with nothing moved. It is read at:
- `GET /api/v2/org` (backend/app.py): the CEO Bot is left out of `bots`; bots and seats that hang under it go to the next row up (the same walk used for a bot the caller may not see); `reports_to` stays as stored.
- `GET /api/employees` (`employee_rows`, backend/views.py): every bot is still listed, with unmoved `org_parent`; the CEO Bot's row gets `chart_hidden: true`, and only the sidebar chart drops it.
- `GET /api/humans` (people chart, backend/views.py): groups unmoved.
- `seat_parents` (backend/views.py, used by `GET /api/v2/seats`): a fourth call site, added since the ticket's sizing; it reads the same chart.

## Goals tree
It uses the same code path. The Goals tree reads the same `org_parent` and group placement from `/api/employees` and `/api/humans`, so with the setting on, the Heads sit in their own places there too. The CEO Bot itself is still on Goals (its line, its panel, its chat), as the acceptance asks.

## Tests
- `backend/tests/test_ceo_bot.py`: new tests `test_a_person_hides_the_ceo_bot_from_their_own_chart` (org API, employee rows, people chart and seats, compared with the unmoved view; another person's chart and a bot's unchanged; stored `reports_to`, bot_config row, CEO Bot settings and its employee row unchanged; Show restores every view) and `test_hiding_the_ceo_bot_is_a_persons_own_yes_or_no` (boolean only, bots refused and never hidden, per person).
- `ui/tests/ceo-bot.cjs`: new scenario under a renamed bot ("Atlas"): hide by keyboard from the sidebar menu, check the chart and that Goals still has it, Show on my chart from the Settings > Bots row, Hide again from the editor.
- Related browser tests: listed in the receipt on the ticket.

Not run: the full pytest suite (hosted checks run it) and any browser test not named in the receipt. No OpenAPI change: no route or model was added.

Docs: docs/org-chart.md, next to the CEO Bot line.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
