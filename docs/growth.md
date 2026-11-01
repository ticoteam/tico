# Growth

Growth is how the team finds customers and learns what works. It is its own section in the sidebar, under Strategy
(`#/growth`; tabs Ideas, Buyers, Channels and Sources, and a brain icon for Learning). Tico keeps the bets, the channels, the
ideas, the tests, the verdicts and the economics. The tools (CRM, ad accounts, email) stay where they are and Tico reads
their numbers.

The chain is: **bets → channels → ideas → tests → verdicts → learning**. A bet is a claim about the business. A channel is a
way to reach customers. An idea is one test of one belief on one channel. A verdict says what the test showed, and the
learning goes back into the bet.

Examples use Acme, a home-cleaning company. Its bet 1 is "Half the price of other cleaners".

## Buyers

A buyer is a group of customers the company sells to, such as homeowners or property managers. **Buyers**
(`#/growth/buyers`) lists each one's name and who buys. The API and `hub` call them segments. A buyer's status is in its
dialog, and dropped ones fold behind a toggle:

- `serving`: the company sells to them now;
- `testing`: a candidate the team is trying out;
- `next`: the one to enter after;
- `dropped`: no longer pursued.

Each segment has a motion (the same choices as GTM), who buys, the deal size, a why and an owner. Its name and
place live in the market graph as a `segment` entity. Adding a segment by name uses the market segment of that name or
alias, and makes one under the market's root when there is none. **Where we play** on the Strategy page follows Growth:
a serving segment is Us and a next one is Next, and a click there sets the status here. Several of each are allowed.

Ideas name the buyers they reach (`segments`), and the idea form picks them. Channels can name them through the API, but
Growth › Channels shows no buyers and only whole months. A cohort may be one segment's share of a channel's month
(`segment_id`). Economics for one segment (`?segment=`) reads only its rows. Without a segment, a channel's row with no segment is
its whole month when it has one (segment rows beside it are a breakdown, not added again); otherwise its segment rows
are added into one. The GTM summary bots read names the serving and testing
segments.

The owner and admins add segments and change any field, status included. A bot adds a `testing` segment and changes a
segment's why, who buys and deal size.

## The Idea

An idea is a task of type **Idea**, with a row of growth fields. Everything a task does (owners, subtasks, comments,
history, `blocked_by`) works on an idea the same way.

| Field | What it holds |
|---|---|
| `hypothesis` | "If we X for Y, Z moves because W" |
| `prediction` | A number, on a KPI, by a date. Example: "Price-request bookings 22% to 30% in four weeks." |
| `bet_id`, `kpi_id` | The bet it serves, and the KPI it moves |
| `channels` | One or more channel slugs. An idea across three channels is counted once, not split |
| `segments` | The segments it targets |
| `ice_i`, `ice_c`, `ice_e` | Impact, confidence and ease, 1 to 10 each. `ice` is the average, to one decimal |
| `source_kind`, `source_ref` | Where the signal came from: `strategy`, `competitor`, `listening`, `peec`, `lost_test`, `person` or `other` |
| `test_from`, `test_to`, `change`, `stop_if`, `min_events`, `judged_kpi_id`, `audience` | The test, once it starts |
| `result`, `learning`, `bet_effect`, `confidence`, `verdict_at`, `verdict_by` | The verdict |
| `follow_up_of`, `depends_on` | The idea it follows up, and the ideas it waits for |

**Status** is one of `idea`, `testing`, `paused`, `won`, `lost`, `inconclusive` or `dropped`. An idea starts at `idea`. It
moves to `testing` when a test starts. A verdict moves it to a result and closes the task.

- **Subtasks** are the work. Each subtask that a channel owner does carries the label `channel:<slug>`, so the **Working
  now** list can show who is doing what for which idea.
- **Follow-up**: a new idea that follows a lost or inconclusive one is linked with `follow_up_of`. A follow-up tests the
  same belief another way.
- **Depends on**: an idea that waits for another is linked with `blocked_by` between the two idea tasks.

Anyone may file an idea. Changing an idea needs an admin, its assignee or its creator. A verdict needs an admin or the assignee.

## Ideas and KPI views

**Ideas** (`#/growth`) is a grid of bets and channels, with the ideas in each cell: testing first, then ideas, then results,
which are greyed. Each shows its status. On a phone it is a list grouped by bet.

The KPIs are read on Strategy › Goals (`#/strategy/goals`), with a switch:

- **Tree**: the one KPI the company is judged on (the North Star), and the inputs under it. A KPI has a `parent_kpi_id` and a
  `growth_role` of `north_star`, `input` or `guardrail`. There is one North Star; setting a new one clears the old.
- **Loops**: KPIs grouped by `loop_name` and `loop_step`, for a loop such as referrals (invite, first clean, referral).
- **Journey**: KPIs by `stage`: `reach`, `acquire`, `activate`, `retain`, `refer`.

Whoever may edit a KPI sets these from its row, or with `hub kpi update` (see [Goals and KPIs](goals-and-kpis.md)).

## Verdicts

The verdict is written by the idea's owner (the Growth Lead by default) and stands unless the owner changes it. The rule
is shown on the idea page, and this is the same rule:

1. **Lost** if the stop rule was hit, or the judged KPI did not beat its baseline in the predicted direction.
2. **Inconclusive** if there are fewer events than `min_events`, or the result is between the stop rule and the win share.
3. **Won** if the result reached the win share of the prediction. The win share is 60% by default
   (`hub growth settings`, `verdict_win_pct`), and the baseline must be beaten.

Brand-track ideas always carry a confidence (`low`, `medium` or `high`).

Example: the prediction was 8 more price-request bookings a week, from a baseline of 14. The window averaged 19. That is
5 of 8, or 62%: **Won**. The bet effect is `supports`.

A verdict is evidence for the bet it serves. **Learning** (`#/growth/learning`) lists the verdicts, and each bet shows its
supports, weakens and none counts (`hub growth evidence`).

## Channel economics

Tico computes these for each channel and for the total, every month. It never stores them, so they update as the inputs do.

- **CAC** = (spend + bot cost) ÷ new customers. Blank when there are no new customers.
- **Expected months** comes from retention at 30, 90 and 180 days: a curve through those points, with a tail for later
  months, capped at 60.
- **LTV** = revenue per month × margin × expected months.
- **LTV:CAC** = LTV ÷ CAC.
- **Payback** = CAC ÷ (revenue per month × margin), in months.

**Where the inputs come from.** Spend comes from Finance, tagged by channel, or the owner's number. New customers and
retention come from the CRM (read live, never copied into Tico), using the source field that `hub gtm sources` names.
Bot cost is what the channel's bot work cost. Each row says its quality: `actual` when every input came from a source,
`estimate` when one did not.

**Unknown.** A customer with no source, or a source that is not a channel, goes to the **Unknown** row. Tico never decides
which channel a customer came from. The Unknown row has no CAC and no LTV.

**A month with no numbers** still lists every active channel and Unknown, each blank, so the owner can enter the first
month from its row on Growth › Channels (+ Month).

**Guardrails.** A channel can name a KPI and a maximum (for example, first-month churn at most 10%). A breach shows on the
channel.

## The weekly loop

Each Monday the Growth Lead (the Head of Marketing template) runs the weekly growth meeting, in one turn:

1. Reads the week: readings, tests due, top ideas, verdicts and breaches, from `hub growth week`.
2. Writes the verdicts that are due, and scores new ideas that have no score.
3. Picks tests to start, within a capacity rule its playbook sets (how many run at once, and per channel owner).
4. Asks the owner one multi-pick question, with the recommended picks ticked. `--spend` names one pick: ticking that option
   approves its money, and no other option does. Any other paid pick needs its own approval before it starts. Picks start
   after the owner's answer, or on the reversible default the question names, never before. Picks the owner does not
   answer go ahead only when they cost nothing.
5. Starts each picked idea, files the channel owners' subtasks, and waits for the owner's answer on the question.

The owner's week is one question. The Head of Marketing's three routines are on after setup: the weekly summary by setup
itself, and the two growth routines by its onboarding playbook (step 6, `hub automation update <id> --enable`).

## The monthly report

On the 3rd, the Growth Lead writes the month's report as a Page:

1. Enters each channel's cohort for the month (`hub growth cohort set`), from the sources, one per segment
   (`--segment`) when the source can tell them apart.
2. Reads the economics for the month.
3. Writes the top line, the channel table with its method, what was learned, which bets are working, and next month.
4. Asks one question for any budget or stage change.

The report links to the Finance report for the books. It does not copy them.

## Brand and GTM cards

Brand and GTM feed Growth and every bot that writes outward. Brand is a strip on the Strategy page and opens at
`#/strategy/brand`. GTM is read and edited under the list on Growth › Buyers, where Edit saves the draft and publishes it
with a note. Its sources are on Growth › Sources, and its versions on Learning.

**Brand** is the brand book: the brand name (when it differs from the company's), the positioning line, voice traits (each with a do and a don't), tone by situation, the
words to use and to avoid, naming rules, claims and proof, visual files (as links to Library Files) and examples.
`hub brand check` runs the cheap checks on a draft: avoided words, naming, and `claims.never` phrases. It runs no model
and does not detect claims missing from the allowed list, so a bot checks those against the claims list itself.

**GTM** is the go-to-market card: the motion (self-serve, sales-led, partner, marketplace, local, community or
e-commerce), the buyer and deal size, the objections, the **sources** (which system holds each number), and the **wishes**.
A wish is a plain sentence, such as "No cold SMS", with a level: `block`, `warn` or `setting`. Wishes become rules, and a
blocking wish stops the work it names.

Each card is versioned and published on its own. The owner edits the draft and publishes. A bot that wants a change
proposes it (a `brand_draft` or `gtm_draft` proposal), and the owner confirms it. Anyone may read the published version
with the CLI or MCP.

```
hub brand show [--section voice] | summary | check -      hub brand draft --section S --body JSON | publish --note N
hub gtm show   [--section wishes] | summary | sources     hub gtm draft --section S --body JSON | publish --note N
```

## Commands

Every command has an MCP tool with the same name and arguments (`growth_idea_create`, `brand_check`, `gtm_show`, and so on).

```
hub growth segment list | create --name N|--segment-id ID [--status S] [--motion M] [--who-buys T] [--deal-size T]
                   [--why T] [--owner O] | update ID
hub growth channel list | show SLUG | add | update SLUG | retire SLUG      (add, update: --segment ID, repeatable)
hub growth source  list | add | update ID | retire ID
hub growth idea    create | list [--segment ID] | show ID | update ID | score ID --i N --c N --e N
                   | start ID --to DATE --change T --stop-if R [--from D] [--min-events N] [--judged-kpi ID] [--audience A]
                   | verdict ID --status won|lost|inconclusive|dropped|paused [--result R] [--learning L] [--bet-effect E] [--confidence C]
hub growth cohort  list [--segment ID] | set --channel SLUG|unknown --month YYYY-MM [--segment ID] [--spend N] [--bot-cost N]
                   [--new N] [--d30 N] [--d90 N] [--d180 N] [--revenue N] [--margin N] [--quality actual|estimate]
                   [--source FIELD=TEXT]
hub growth economics [--month M] [--segment ID] | week | evidence | working | kpis | settings [--set k=v]
hub kpi update ID --parent P --role north_star|input|guardrail --stage S --loop NAME:STEP
```

The API is under `/api/v2/growth`, `/api/v2/brand` and `/api/v2/gtm`.

## Who may do what

| | Read | Propose | Write |
|---|---|---|---|
| Owner and admins | everything | none needed | everything, including publishing Brand and GTM, segments, channels, sources and settings |
| Brand Manager, Strategy Bot | published Brand and GTM | Brand and GTM changes | nothing |
| Growth Lead | everything | none | ideas it owns, scores, verdicts and cohorts |
| Channel owner bot | published Brand and GTM; its channel | none | its channel's `why`, stage and budget; its measurements as KPI readings; its own ideas and subtasks |
| Any other bot, person, or external agent with hub access | published Brand and GTM | ideas (filed as `idea`) | its own ideas only; a bot also adds testing segments and edits a segment's why, buyer and deal size |

Brand and GTM are read by anyone with hub access, bots and external agents included. A change to either is the owner's to
publish: a bot proposes it, and the owner confirms.

## Related

[Strategy](strategy.md), [Goals and KPIs](goals-and-kpis.md), [Routines](routines.md), [Tasks](tasks.md),
[Finance tools](finance-tools.md) and [Your Strategy Bot](strategy-bot.md).
