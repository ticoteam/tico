# Strategy

**Strategy** (`#/strategy`) is where a team writes down the few bets it is making, keeps them honest against the
market and the numbers, and makes the org chart and the goals follow from them. The page has one fixed shape: a
summary every bot reads on every run, three to five **bets**, where the team plays in its market, its competitors,
related news, and four sections. The owner writes and
publishes it; the [Strategy Bot](strategy-bot.md) proposes; the [Librarian](librarian.md) keeps the evidence under it current.
**Market** is a tab of the section (`#/strategy/market`; `#/market` redirects there), unchanged in what it does.

**Market views.** The Market tab shows the market in views: Players (the graph), Spend, Buyers, Value chain and
Size vs growth. Spend is the default once a segment is sized. How the market is defined, cut into segments and sized
is in [Librarian](librarian.md#market-spend).

Three activities make one loop:

| Activity | Who | What it produces |
|---|---|---|
| Craft the strategy | the owner, with the Strategy Bot proposing | the Strategy page: summary, bets, four sections |
| Craft the market | the Librarian | the market graph and pages under the Market tab |
| Keep it honest | the Strategy Bot | a Decide card when a bet's wrong-if trips |

Results close the loop: a goal links to a bet, and the bet's health is the colour of its KPI-backed goals and nothing
else. A goal opens to the reason it exists: "bet 2, which cites this market entity and is wrong if churn passes 4%".

This page is the shape, lenses, who may do what, setup, publishing, the fit check, what bots see, and the API.

## The page

The page is cards, in this order: the summary on top; **Where we play** beside the **Bets**; **Competitors** beside
**Related news**; then the sections. The owner edits each in place; a × on hover (always shown on a touch screen)
removes a thing.

- **Brand.** The Brand strip (name, positioning, colours, voice, never-say) opens the brand book, which is versioned on
  its own and published by the owner. See [Growth](growth.md#brand-and-gtm-cards).
- **Growth.** Its own section under Strategy in the sidebar (`#/growth`; the old `#/strategy/growth` and
  `#/strategy/gtm` addresses redirect): ideas, buyers and go-to-market, channel economics, sources, tests and verdicts.
  The KPI views (Tree, Loops, Journey) are on the Goals tab. See [Growth](growth.md).
- **Summary.** At most 400 characters, drafted from the bets and edited by the owner. It is the one line on top and the
  text every bot gets in its run context.
- **Vision.** Stored, not shown on the page. The Goals page reads it from here and, when it is empty, reads the
  summary instead. Setup no longer drafts one; one written earlier, or by the Strategy Bot or `hub strategy draft
  --vision`, is kept and published as before.
- **Bets.** Three to five recommended, at most seven active. The page shows each bet's statement, edited in place,
  with its goals and health. **+ Bet** adds one, active while fewer than seven are (else proposed); × retires one
  (with Undo), and **Retired** lists them with **Restore**. A proposed bet's **Proposed** pill makes it active. The
  number beside a bet is its handle: drag it, or press the arrow keys on it, to reorder. Each bet has:
  - `statement`: one sentence that could turn out wrong;
  - `why`: the reasoning, one or two sentences;
  - `rules_out`: what it refuses. Optional, not shown on the page;
  - `wrong_if`: the observation that would retire it, with a check-by date. Optional, not shown on the page; the
    [fit check](#the-fit-check) watches only bets that have one. It is
    one of a KPI with a threshold ("monthly churn above 4%"), a market entity or page the bet rests on (the bet
    trips when it changes), or a sentence. A sentence-only wrong-if shows as **not yet measurable**;
  - `cites`: the market entities or pages the bet rests on (optional);
  - `status`: `proposed`, `active` or `retired`. A retired bet keeps its goals.
- **Where we play.** The market's segments by spend ([Librarian](librarian.md#market-spend)), a sized segment inside
  its sized parent, unsized ones as plain tiles. A segment the team plays in is **Us** (filled) and one it means to
  enter next is **Next** (outlined); a click on a segment makes it Us, then Next, then neither, and several segments
  may be each. × removes a segment (its sub-segments move up to its parent) and **Add a segment** adds one by name
  (defining the market first, as "<company> market", when there is none). These are the market graph's own rows: Us is
  `company/self` `operates_in` the segment, Next is `next: true` on the segment, as the company file's import writes
  them. They mirror the [Growth buyers](growth.md#buyers): Us is a serving segment and Next a next one, and a
  change on either page shows on the other. A write straight to the graph (the market routes, an import) moves the
  Growth segment too: Us makes it serving, Next next, and losing both drops a serving or next one. A segment merged
  into another hands it its Growth segment (or folds into the one it has, keeping the stronger status, with its
  channels, ideas and cohorts). Only a person marks Us or Next here; a removed segment loses its Next, so adding it
  again does not bring it back.
- **Competitors.** Every company or product with a current `competes_with` edge to `company/self`, as chips that link
  to their sites. × ends those edges (the company stays in the graph); **Add a competitor or its site** finds the
  company the graph has by that name or domain, or makes it, and adds the edge.
- **Related news.** What Listening found: the newest posts routed to a destination the reader reads, and the posts
  setup filed as market insights (`about` "Listening: hot" and so on; the owner's only), hot first, then the newest,
  eight at most, each linking out.
- **Four sections**, markdown, in this order, in the owner's words:
  1. **Who we serve** (`serve`): the customer, the alternative they use today, the one you turned down.
  2. **What we sell, and for how much** (`sell`): the product and the pricing model, and the price that would break if
     doubled or halved.
  3. **What we know that others don't** (`know`): the thing the team understands about the business that the obvious
     competitors miss. This is where "how we win" lives, and it is the heart of the page.
  4. **What we won't do** (`not`): required before publishing. A strategy with no trade-offs is a description.
- **Extra sections.** The owner can add any heading (brand, channels, capabilities). They are stored by key and never
  dropped.
- **Versions.** Publishing makes a numbered version with a note. Older versions stay readable. The page lists them and
  opens one; the API also has a diff (which fields changed, which bets were added, retired or rewritten).

### An example

Acme is a fictional cleaning team. Its page, in short:

- **Bet 1:** Charge half what other cleaning companies charge, because bots do the office work. (Kept in the data,
  not shown: *rules out* a premium tier and one-off deep cleans; *wrong if* monthly churn among property managers
  passes 4%, checked by 31 March.)
- **Where we play:** Home cleaning is Us, Office cleaning is Next.
- **Competitors:** CleanCo, MaidPro.
- **Who we serve:** property managers, who use a local cleaner today.
- **What we know that others don't:** the office work (booking, scheduling, billing), not the cleaning, is what makes
  cleaning expensive.
- **What we won't do:** a premium tier, one-off jobs, phone support, new cities yet.

### Health

A bet's health (`health_by_bet`) is worked out from its **KPI-backed** goals only: the colour of their KPI
arithmetic, never a goal's lifecycle status. A bet whose goals have no KPI shows **gray**, because a green "ship the
pricing page" says nothing about whether a pricing bet is right. Linking a goal to a bet is optional; the page offers
"not tied to a bet" as a filter, never as a nag.

## Lenses

A **lens** is a set of questions from a known framework, run once against the published strategy (or your draft, if
you are the owner) or the market. The result is a summary, sections, a chart, findings about your bets and changes you
may add to the draft one at a time; nothing is published or written by itself. The listed lenses are Playing to Win,
Blue Ocean, Scenarios, Seven Powers, Good Strategy, Pre-mortem and Positioning for the strategy, and Where to Play and
Five Forces for the market. They are in [Lenses](lenses.md).

The Strategy Bot's setup questions are the **Default** guide, in the style of the YC application and a16z's go-to-market
writing: what you make and who needs it; what you understand that others don't; how you make money and how much;
who you fear and what stops a bigger company copying you; the wedge you enter through; the price that breaks; the
request you turned down; the bet most people would call wrong. It is "in the style of", not theirs. The other guides
became lenses; `hub strategy guides` and `GET /api/v2/strategy/guides` are deprecated and list the Default guide only.

## Who may do what

| | Read | Write the draft and bets | Publish |
|---|---|---|---|
| The owner | the draft and every version | yes, on the page or with `hub strategy draft` and `hub strategy bet` | yes, with a note |
| Any human on the team | the published versions | no | no |
| The Strategy Bot | the published versions | no: it proposes, at every level, with one `strategy_draft` proposal the owner confirms | never |
| Every other bot, and an external agent over MCP | the published versions (`hub strategy show`, `hub strategy summary`) | no | never |

**The draft is the owner's.** `GET /strategy` returns `draft` only to the owner (`may_edit`); everyone else, every
bot included, sees the published version. A bot never sees an unpublished "What we won't do". The draft carries a
`revision`, so two editors get a conflict (`409`), not a silent overwrite.

If you see **Changed elsewhere, reloaded**, review the refreshed draft and reapply your edit.
A failed bet move also reloads the list; check the order before trying again.

The Strategy Bot proposes at every level, Passive included (in chat when asked). A `strategy_draft` proposal is partial:
any of the summary, the vision, sections and bets. Confirming it writes those into the draft as the owner; nothing is
published. Publishing is the one click that tells the whole team something changed, so it stays a person's. The
owner's level choice for the Strategy Bot is on [Your Strategy Bot](strategy-bot.md#the-three-levels).

## What the Strategy Bot does with it

Strategy is the Strategy Bot's first job ([Your Strategy Bot](strategy-bot.md#strategy)). Its playbooks are the product
(`templates/catalog/goal-manager/playbooks/`):

- **Draft the strategy** (`draft-the-strategy.md`): a task "Draft the strategy", or the owner asking in chat. It reads
  the market pages, the setup answers, the goals and KPIs, and turns the default guide's questions into **claims to
  confirm or strike**, not open questions: "Your site implies you compete on price; that rules out white-glove
  service. Keep, edit or strike." At most eight claims a run. It proposes three to five bets, each with a statement,
  why, rules out and wrong if (a KPI with a threshold if it can, else a market id, else a sentence with a check-by
  date), fills the four sections, and files one `strategy_draft` proposal. It never invents a number: a figure or a
  threshold appears only when a source or the owner states it. It ends by asking the owner to confirm and publish.
- **Bet tripped** (`bet-tripped.md`): see [the fit check](#the-fit-check).
- **Goals from the bets**: when the owner asks for goals, or after a version is published, it suggests one or two per
  active bet, each with `bet_id` set and a KPI that could serve as the bet's wrong-if. The Monday note names each
  active bet with no KPI-backed goal and each bet whose health is gray.

The Librarian reads the strategy too: "Set up the market map" takes the team's website from `hub strategy show` when
its task has none, and when an insight contradicts an active bet the daily curate routine says so in its report and
sends the Strategy Bot a note, so it can propose the entity as one of the bet's `cites`. The Librarian never writes the
strategy ([Librarian](librarian.md#setting-up-the-market)). The optional Strategy Analyst template reads the bets from
`hub strategy show` and plans each quarter against them; it keeps no strategy notes of its own.

## Setup

Setup starts with **What are you building** and **Your bets** ([Onboarding](onboarding.md#the-screens)):

1. **What are you building.** An existing team pastes its website and one line; a new idea is described in a few
   sentences. With a URL the server fetches home, about, pricing and customers right away, fills the summary and
   the four sections (each section marked **unconfirmed**), and proposes three bets the site implies, each
   with what it rules out.
2. **Your bets.** Claims to confirm or strike, not open questions. The page fills in on the right as the owner answers.
   **Keep** makes a bet active; what it rules out and its wrong-if are optional, and shown only when set. To leave
   the step: one active bet and one line under *What we won't do*. Without a
   model provider, or when the research failed, it is the same fields as a plain form; when the research gave up it
   lists what it found, each with **Add**. The step saves the **draft**; nothing is published.
3. **Org.** Recruit proposes groups and bots from the bets and names the bet each serves.
4. Computer, agent and review as before. The screen after Create says **Strategy: a draft. Publish your strategy**
   (a link here) until a version is published, which ticks Getting started's `strategy` step; a published version
   is when the Strategy Bot suggests goals per bet. With a website, Create files the Librarian's market task.

The wizard saves the website as `names.website` and the one line as `answers.building`
([Onboarding](onboarding.md#the-answers)), and `POST /api/v2/strategy/draft-from {url?, text?}` (owner only) starts
the research as a server job, answered at once with the job; `GET /api/v2/strategy/draft-from/{job}` (or `latest`)
polls it:

| `state` | Meaning |
|---|---|
| `running` | `step` is `reading` (fetching pages) then `thinking` (the model call) |
| `done` | `written` is `{sections, fields, bets, revision}`: the sections filled, which fields were filled (only `summary` now), the proposed bet ids, the draft's new revision |
| `gave_up` | the owner wrote the draft meanwhile (`error.code` `edited`) or a newer job started (`superseded`); nothing was written and `found` holds what the research found, to offer by hand |
| `failed` | `error.code` is the fetch code (`private_address`, `timeout` ...), the model's, `empty`, `interrupted` when the server restarted mid-run (or it ran 10 minutes; it then writes nothing), or `cleared` when setup's website and line were cleared |
| `no_model` | no model provider: the step is a plain form |

Every job also carries `url`, `text`, `pages` (`[{url, title}]` or `{url, error}`, never page text), `found`
(`{summary, serve, sell, know, not, bets: [{statement, why, rules_out, wrong_if}]}` once the model answered; it
drafts no vision, and the draft's `vision` is left as it is)
and `started`/`finished`;
the last five jobs are kept. The fetch is the doc-link fetch's guard: http and https only, every address the name
resolves to must be public, each redirect re-checked, at most four pages of 8,000 characters in 45 seconds, and no
server credentials sent. The model call is the one-shot path the update writer uses (the Tico AI Gateway, else the
team's own provider key); no runner or agent turn is involved. Its prompt and answer schema are
`backend/strategy_prompt.py`, a file with no imports that Tico HQ's setup page keeps a pinned copy of, so both draft
the same fields; it never invents a number (a price, count or share appears only when a page or the owner states it).
The summary and sections are written only where empty, each section starting
`_Unconfirmed: drafted from acme.example._`; bets are added `proposed` with the suggested wrong-if as a text test
due in 90 days. Nothing is published. There is no `hub` or MCP verb for it: it is a browser setup step.

Finishing setup links each group it makes to the bet it clearly serves (`bet_id`), and with a website files the
Librarian's market task ([Onboarding](onboarding.md#what-create-does)).

An install set up before these steps starts the Strategy page from what the Goals page held: one with a Vision or a Description and no
strategy gets a draft with that vision and the description as *What we sell*, for the owner to take from there. The
Market tab's own first run (the Librarian's four questions) is unchanged ([Librarian](librarian.md#first-run-setup)).

## Publishing

The owner edits the draft and publishes with a note. **Publish** refuses, and the button says why, until:

- at least one bet is active (what it rules out and its wrong-if are optional), and
- *What we won't do* is not empty.

Changes only to a bet's market citations or the guide also enable **Publish** when these conditions hold.
Saving the summary while a bet editor or publish form is open does not prevent you from submitting that form.

Publishing snapshots every bet that is not retired, with its status, as version *n*, and opens a fresh draft copied
from it. Then:

- **Every bot** gets the new summary and active bets in its run context from its next run, under **Team strategy**
  ([Agent context](agent-context.md)). There is no note to each bot.
- **A `strategy.published` routine event**, subject `v{n}`, for a routine that should run at once
  ([Routines](routines.md)).
- **One realignment card** in the owner's Needs you, when a bet was retired or its statement changed: it lists the
  goals tied to those bets, with **retire goal**, **move to bet N** or **keep** for each. Bot instructions that still
  say the old positioning are the owner's to fix for now.

## The fit check

The server watches each active bet's wrong-if: a KPI reading crossing its threshold (from the KPI status pass), a
change to a cited market entity or page (from market events), or a sentence bet's check-by date passing. When one
trips, once per bet per version, it files a task for the Strategy Bot, "Bet N: may be wrong", with the evidence.

The Strategy Bot's `bet-tripped.md` playbook reads the bet, the evidence, the readings or `hub market show <id>`, and the
goals linked to the bet, and writes **one Decide card** for the owner: a `strategy_draft` proposal that retires the
bet, rewrites it, or keeps it with a new wrong-if, with its pick and why in a few lines. It never edits goals. This
runs at every level: at Passive the server still files the task, and the Strategy Bot only writes the card.

The Librarian's [weekly briefing](market-briefing.md) feeds the same check from the other side: when it touches an active
bet, the Strategy Bot weighs that bet and, only when it is no longer right, files one proposal marked as from the briefing. The
[page monitors](market-watch.md) turn a competitor's changed pricing page into an insight; a bet trips on it only once the
Librarian applies it to the entity.

## What bots see

- **In context**, every turn once a version is published: `GET /strategy/summary` under **Team strategy**, about 500
  characters: the summary, one line per active bet with its wrong-if, then "version N". Nothing while no version is
  published.
- **On request**: `hub strategy show` (the published page with bets, `goals_by_bet` and `health_by_bet`) and
  `hub strategy summary`.
- Never the draft.

## `hub` and the API

```
hub strategy show                       the published version: bets, goals_by_bet, health_by_bet (the draft too, for the owner)
hub strategy summary                    the text every bot gets in context
hub strategy draft [--summary S] [--vision V] [--section KEY=TEXT ...] [--extra KEY --title T --body B]
                                        owner only; KEY is serve, sell, know or not; "" clears a key
hub strategy publish --note "<why>"     owner only
hub strategy versions                   n, note, who published, when
hub strategy diff <from> <to>           fields changed; bets added, retired, changed
hub strategy bet add --statement S [--why W] [--rules-out R] [--wrong-if-kpi ID --wrong-if-direction above|below
    --wrong-if-threshold N | --wrong-if-thesis MARKET_ID | --wrong-if-text TEXT] [--check-by DATE] [--cite MARKET_ID ...]
    [--status proposed|active] [--rank N]
hub strategy bet update <id> [the same flags]      hub strategy bet retire <id>      (owner only)
hub strategy guides                     deprecated; the Default guide only
hub lens list|run|show|runs            see Lenses (lenses.md)
hub goal create ... --bet <id>          hub goal update <id> --bet <id>
```

Every command has an MCP tool of the same name and arguments (`hub_strategy_show`, `hub_strategy_bet_add`,
`hub_strategy_bet_update`, `hub_strategy_bet_retire` ...). The routes, under `/api/v2`:

| Route | Who | What it does |
|---|---|---|
| `GET /strategy` | any reader | `{draft, current, bets, label, label_one, may_edit, goals_by_bet, live_by_bet, health_by_bet}`; `draft` only when `may_edit`; `goals_by_bet` lists only goals the caller may read, at most 20 per bet; `live_by_bet` counts every one of those not done or dropped; `health_by_bet` is a colour from KPI-backed goals, `gray` otherwise; each section at most 4,000 characters |
| `PUT /strategy/draft` | the owner only | partial merge of `{summary?, vision?, sections?}`; `""` clears a key; needs `revision`, answers `409` on a mismatch; summary and vision at most 400 characters |
| `POST /strategy/publish` | the owner only | `{note, revision}`; refuses unless the [preconditions](#publishing) hold; answers the new version |
| `GET /strategy/versions`, `/strategy/versions/{n}` | any reader | the list, newest first; one version |
| `GET /strategy/diff?from=&to=` | any reader | `{fields: [{key, from, to}], bets: {added, retired, changed}}`, keyed by bet id and status |
| `POST /strategy/bets`, `/strategy/bets/{id}` | the owner only | create; partial update including `rank` and `status`; at most seven active, at most 30 not retired; `rules_out` and `wrong_if` optional |
| `POST /market/segments/target` | the owner (a bot, the Librarian too, gets 403) | `{id, target: ours\|next\|none}`: the segment becomes Us, Next or neither, other segments unchanged; its Growth segment becomes serving, next or (for neither) dropped, a testing one staying testing; answers `{id, ours, next}` |
| `POST /market/segments/remove` | the owner or the Librarian | `{id}`: retires the segment (its Next cleared, its Growth segment dropped) and moves its sub-segments up to its parent; not the root |
| `GET /market/competitors` | any reader | `{competitors: [{id, name, url}]}`, by name |
| `POST /market/competitors` | the owner or the Librarian | `{name}`: a name or a site; answers `{competitor}` |
| `POST /market/competitors/remove` | the owner or the Librarian | `{id}`: ends its `competes_with` edges; 404 if it is not one |
| `GET /strategy/summary` | any bot or human | plain text: the summary, one line per active bet with its wrong-if, "version N" |
| `POST /strategy/draft-from`, `GET /strategy/draft-from/{job}` | the owner | setup research from `{url?, text?}`: fills `serve` and `sell` marked unconfirmed and proposes bets; gives up, reporting what it found, if the owner wrote the draft meanwhile. States are in [Setup](#setup); 422 for a missing or internal address |
| `GET /strategy/guides` | any reader | deprecated: the Default guide only |
| `/lenses/...` | see [Lenses](lenses.md#hub-and-the-api) | list, run, read, add a change to the draft, sharpen |

A bet's `wrong_if` is `{kind: kpi|thesis|text, kpi_id?, direction?, threshold?, market_id?, text?, check_by}`. The
`strategy_draft` proposal's payload is partial `{summary?, vision?, sections?, bets?: [{id?, statement, why?,
rules_out?, wrong_if?, cites?, status?, ideas?}], source?, lens?}` (the server sets `source: "lens"` and `lens` for a lens run). A bet's `ideas` are growth idea task ids that serve it; confirming links each idea to that bet. Goals take `bet_id` on `POST /goals` and `POST /goals/{id}` and return
it in `GET /goals` and `/goals/tree` ([Goals and KPIs](goals-and-kpis.md#hub-and-the-api)); goal proposals carry it
too. Every change is a row in `strategy_events`, like `market_events`.

Related: [Growth](growth.md), [Goals and KPIs](goals-and-kpis.md), [Your Strategy Bot](strategy-bot.md), [Librarian](librarian.md),
[Routines](routines.md), [Agent context](agent-context.md).
