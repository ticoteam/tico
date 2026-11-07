# Strategy

**Strategy** (`#/strategy`) is where a team writes down the few bets it is making, keeps them honest against the
market and the numbers, and makes the org chart and the goals follow from them. The page has one fixed shape: a
summary every bot reads on every run, three to five **bets**, where the team plays in its market, its competitors,
related news, and its sections. The owner writes and
publishes it; the [Strategy Bot](strategy-bot.md) proposes; the [Librarian](librarian.md) keeps the evidence under it current.
**Market** is a tab of the section (`#/strategy/market`; `#/market` redirects there), unchanged in what it does.

**The Market page.** The Market tab is one page that reads top to bottom. Under the market's name are three tiles:
yearly spend (with its growth, and **Sure** or **Estimated**), the fastest-growing segment, and our share of the
spend. Then five questions, each with a one-line answer the Librarian writes with the briefing
([Market briefing](market-briefing.md)), and each left out when there is nothing to show:

- **What changed?** The latest briefing's items, with their kind and the bets they touch. **Read** opens the briefing.
- **Where is the money going?** The treemap of spend by offering or by buyer, beside size against growth. A box or a dot
  opens its arithmetic. Before anything is sized, the owner describes the market in a sentence here and the Librarian
  sizes it.
- **Who are we up against?** The players in the segments with what they sell, their revenue and share where known, and
  the briefing's last word on each. A row opens the player's note.
- **How does it reach buyers?** The value chain, with spend or revenue on each node where known.
- **What are buyers saying?** Listening's posts a day and the newest posts, for those who can read them. Owners and
  admins see **Settings** for where posts go.

**Open map** opens the graph and its notes (`#/strategy/market/map`); a note link opens the map at that note. The old
view addresses (`#/strategy/market/briefing`, `/spend`, `/buyers`, `/growth`, `/value-chain`, `/listening`) open the page
at their question, and `/players` opens the map. How the market is defined, cut into segments and sized is in
[Librarian](librarian.md#market-spend).

Three activities make one loop:

| Activity | Who | What it produces |
|---|---|---|
| Craft the strategy | the owner, with the Strategy Bot proposing | the Strategy page: summary, bets, sections |
| Craft the market | the Librarian | the market graph and pages under the Market tab |
| Keep it honest | the Strategy Bot | a Decide card when a bet's wrong-if trips |

Results close the loop: a goal links to a bet, and the bet's health is the colour of its KPI-backed goals and nothing
else. A goal opens to the reason it exists: "bet 2, which cites this market entity and is wrong if churn passes 4%".

This page is the shape, lenses, who may do what, setup, publishing, the fit check, what bots see, and the API.

## The page

The page is cards, in this order: the summary on top; **Where we play** beside the **Bets**; **Competitors** beside
**Related news**; then the sections. The owner edits each in place; a × on hover (always shown on a touch screen)
removes a thing.

- **Brand.** The Brand strip (name, positioning, colours, voice, never-say) opens the brand book at `#/strategy/brand`.
  The book holds the brand name (when it differs from the company's), the positioning line, voice traits (each with a do
  and a don't), tone by situation, the words to use and to avoid, naming rules, claims and proof, visual files (links to
  Library Files) and examples. It is versioned on its own and published by the owner; growth-facing bots get it in their
  run context. `hub brand check` runs the cheap checks on a draft (avoided words, naming, `claims.never` phrases) and runs
  no model, so a bot checks claims missing from the allowed list itself.
- **Growth.** A place of cards (`#/growth`, under Company in the sidebar): each channel's spend, customers and
  retention by month, the economics, and **Sources**, where each number comes from, linked from the page. Marketing tasks are on the Tasks page, type Marketing.
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
  its sized parent, unsized ones as plain tiles. The segments the company plays in are marked. A click on a segment
  toggles its mark, and several may be marked. × on a marked segment takes it out of the company (its teammates' tags go,
  and it stays in the market); × on any other segment retires it from the market, its sub-segments moving up to its
  parent. **Add a segment** adds one to the market by name (defining the market first, as "<company> market", when there
  is none). The mark is the market graph's own row: `company/self` has a current `operates_in` edge to each segment the
  company plays in, as the company file's import writes them. Only the owner marks a segment or takes one out. A segment
  is the company's again only when the owner marks it. [Segments](#segments) says what a segment is once it is the
  company's.
- **Competitors.** Every company or product with a current `competes_with` edge to `company/self`, as chips that link
  to their sites. × ends those edges (the company stays in the graph); **Add a competitor or its site** finds the
  company the graph has by that name or domain, or makes it, and adds the edge.
- **Related news.** What Listening found: the newest posts routed to a destination the reader reads, and the posts
  setup filed as market insights (`about` "Listening: hot" and so on; the owner's only), hot first, then the newest,
  eight at most, each linking out.
- **Seven sections**, markdown, in this order, in the owner's words:
  1. **Who we serve** (`serve`): the customer, how badly they need it, what they use today, the one you turned down.
  2. **What we sell, and for how much** (`sell`): the product and the pricing model, and the price that would break if
     doubled or halved.
  3. **How we know they want it** (`proof`): evidence, not belief. Who uses it, how often, who comes back, what they
     say, each with its source. The Strategy Bot keeps it current ([below](#what-the-strategy-bot-does-with-it)).
  4. **Why now** (`now`): what changed recently that makes this possible or needed now.
  5. **What we know that others don't** (`know`): the thing the team understands about the business that the obvious
     competitors miss, and why this team. This is where "how we win" lives, and it is the heart of the page.
  6. **How we sell** (`how`): where the next hundred customers come from, the motion the company sells with
     (self-serve, sales-led, partner, marketplace, local, community or e-commerce) and the playbooks behind it. Who
     buys is Where we play. Bots read it with `hub strategy show`; it is not put into every run. A bot proposes a
     change the way it proposes any strategy change, and the owner confirms it.
  7. **What we won't do** (`not`): required before publishing. A strategy with no trade-offs is a description.

  Only *What we won't do* is required. A version published before *How we know they want it* and *Why now* existed
  reads them as empty.
- **Extra sections.** The owner can add any heading (brand, channels, capabilities). They are stored by key and never
  dropped.
- **Versions.** Publishing makes a numbered version with a note. Older versions stay readable. The page lists them and
  opens one; the API also has a diff (which fields changed, which bets were added, retired or rewritten).

### An example

Acme is a fictional cleaning team. Its page, in short:

- **Bet 1:** Charge half what other cleaning companies charge, because bots do the office work. (Kept in the data,
  not shown: *rules out* a premium tier and one-off deep cleans; *wrong if* monthly churn among property managers
  passes 4%, checked by 31 March.)
- **Segments:** Home cleaning and Office cleaning, each with its own strategy; Cara Mendes is dedicated to Office.
- **Competitors:** CleanCo, MaidPro.
- **Who we serve:** property managers, who use a local cleaner today.
- **How we know they want it:** 38 new property managers in October; 61% of July's still book at 90 days. (Growth,
  October)
- **Why now:** bots can now do the booking, scheduling and billing a cleaning office does.
- **What we know that others don't:** the office work (booking, scheduling, billing), not the cleaning, is what makes
  cleaning expensive.
- **What we won't do:** a premium tier, one-off jobs, phone support, new cities yet.

### Health

A bet's health (`health_by_bet`) is worked out from its **KPI-backed** goals only: the colour of their KPI
arithmetic, never a goal's lifecycle status. A bet whose goals have no KPI shows **gray**, because a green "ship the
pricing page" says nothing about whether a pricing bet is right. Linking a goal to a bet is optional; the page offers
"not tied to a bet" as a filter, never as a nag.

## Segments

A **segment** is a group of customers the company wins differently. Each has its own line of who it is for, its own
strategy, the teammates dedicated to it, and its goals, budget and P&L line, all under the company's one strategy. A
segment is the market's own: it is the company's when `company/self` has a current `operates_in` edge to it
([Librarian](librarian.md#market-spend)). There is one flag and no second list. A segment the company meant to enter
next before this release is one of its own now; that mark folded into the flag.

**Making and changing them.** The owner adds a segment by name (`POST /api/v2/segments`). A market segment of that name
joins the company's, and a removed one comes back with its id and its history. The owner also renames it, sets its one
line and its colour (blue, green, amber, red, purple, teal, pink or gray; a new one gets the first colour no other
segment has) and orders the list (`POST /api/v2/segments/order` names every segment once). A bot cannot change a
segment. Removing one (`POST /api/v2/segments/remove` with `{id}`, or × on the Where we play card) takes it out of the
company's segments. The market keeps it, with its sizes and its place in the tree, and its teammates' tags go: a tag
names one of the company's segments, so it has no teammate until the owner makes it the company's again, and it comes
back with none. Merging it into another segment moves its bets and the rows keyed by segment (goals, tasks, budgets,
finance contacts and market briefings) to that one, and its teammate tags too when that one is the company's (they go
otherwise). Its draft text fills the sections that one has no text for; where both have text, the one it merged into
keeps its own.

**Who is dedicated.** A teammate, human or bot, tagged with a segment is dedicated to it: one of its **faces**, highest
on the team chart first (the people and bots above it count as its depth; groups do not). A tag of `all` means the
teammate works across every segment. It is not one of their faces, and its runs are handed no segment text: a run gets only the segments it is
tagged with by name. A
teammate may carry several tags. A segment's **leads** are its live dedicated teammates whose manager is not dedicated to
it too; when every one of them has a dedicated manager (a loop in the chart), the highest one leads. Archived bots and
removed people lose their tags. Anyone signed in can read a segment, its faces and its leads. A teammate the viewer may
not see is left out of them, and out of the count.

**Its strategy.** A segment has the same fields as the company's strategy: its summary and the seven sections
([The page](#the-page)). The owner writes its own text for any of them in the draft
(`PUT /api/v2/segments/strategy` with `{id, revision, sections}`, against the draft's revision). A field the segment did not change reads as the
company's text, and `""` gives the company's text back. Its text publishes with the rest, is in every version, and the
diff names it `sections.segments.<id>.<field>`. `GET /api/v2/segments/{id}` gives the strategy field by field: each with
its `text` (the segment's where it changed one; `from` says which), the company's text beside it (`company`) and whether
the segment changed it (`changed`). A bot can propose strategy text for a segment in a
`strategy_draft` proposal, which the owner confirms.

**Bets.** A bet can name one segment, one of the company's, or none ([the API](#hub-and-the-api)). The segment's detail
lists its live bets.

**In a run.** A bot dedicated to a segment is handed, in its run context, each of its segments as
`Segment: <name> — <who>` with the segment's published summary under it. A bot whose only tag is `all` is handed no segment
text; only the segments it is tagged with by name are handed that way. Only published text is handed, never a draft, and at most 2,000 characters in all (a longer
one is cut with `…`). The company's summary still goes to every run, in the same context.

**Who may do what.** Anyone signed in reads the segments. The draft is the owner's, as the company's is. The owner, on
their own click or by a card they confirmed in the Assistant, writes a segment, its strategy and the list, and takes one
out of the company; a bot's request to do so gets `403`. A Librarian may retire a segment that is not the company's; one
the company plays in leaves it on the owner's click only (`/segments/remove`, or a market route the same click
confirms). The owner, on their own click or by a card they confirmed, tags a teammate with a segment; a bot admin, a
bot or an unconfirmed Assistant call gets `403`.

## Lenses

A **lens** is a set of questions from a known framework, run once against the published strategy (or your draft, if
you are the owner) or the market. The result is a summary, sections, a chart, findings about your bets and changes you
may add to the draft one at a time; nothing is published or written by itself. The listed lenses are Playing to Win,
Blue Ocean, Scenarios, Seven Powers, Good Strategy, Startup idea check, Pre-mortem and Positioning for the strategy, and Where to Play and
Five Forces for the market. They are in [Lenses](lenses.md).

The Strategy Bot's setup questions are the **Default** guide, in the style of the YC application and a16z's go-to-market
writing. Twelve questions: what you make; how badly customers need it and what they use today; the wedge you enter
through; how you know they want it; why now; why this team; what you understand that others don't; who you fear and
what stops a bigger company copying you; how you make money, and the price that breaks; where the next hundred
customers come from; the bet most people would call wrong; what you won't do, and the request you turned down. Each is
asked as a claim to confirm or strike. It is "in the style of", not theirs. The other guides
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
  date), fills the sections, and files one `strategy_draft` proposal. It never invents a number: a figure or a
  threshold appears only when a source or the owner states it. It ends by asking the owner to confirm and publish.
- **Keep proof current** (`keep-proof-current.md`): fills *How we know they want it* from the evidence Tico already
  holds: Growth numbers and KPIs, customer-facing bots' tasks, team meetings, Listening, and what those bots learned.
  Each line names its source and period; it never invents a number, and bad news stays in. It files one
  `strategy_draft` proposal. The Monday note adds one line when the section is empty or `proof_since` is more than 30
  days old.
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
   the sections (each section marked **unconfirmed**; *How we know they want it* and *Why now* only when the pages
   show it, such as customer logos, counts or quotes), and proposes three bets the site implies, each
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
(`{summary, serve, sell, proof, now, know, how, not, bets: [{statement, why, rules_out, wrong_if}]}` once the model answered; it
drafts no vision, and the draft's `vision` is left as it is)
and `started_at`/`finished_at`;
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
                                        owner only; KEY is serve, sell, proof, now, know, how or not; "" clears a key
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
| `GET /strategy` | any reader | `{draft, current, bets, label, label_one, may_edit, segment_changes, goals_by_bet, live_by_bet, health_by_bet, proof_since}`; `draft` only when `may_edit`; `segment_changes` is how many segments' text the draft changes against the latest published version (the segments' text itself is `GET /segments/{id}`), 0 with no draft and for a reader, so Publish has a count to go on after a segment-only edit; `proof_since` is when the published *How we know they want it* last changed (null when it is empty or nothing is published); `goals_by_bet` lists only goals the caller may read, at most 20 per bet; `live_by_bet` counts every one of those not done or dropped; `health_by_bet` is a colour from KPI-backed goals, `gray` otherwise; each section at most 4,000 characters |
| `PUT /strategy/draft` | the owner only | partial merge of `{summary?, vision?, sections?}`; `""` clears a key; needs `revision`, answers `409` on a mismatch; summary and vision at most 400 characters; `sections.segments` is `{segment id: {summary?, serve?, ...}}` for the company's segments, `""` clears a key ([Segments](#segments)) |
| `POST /strategy/publish` | the owner only | `{note, revision}`; refuses unless the [preconditions](#publishing) hold; answers the new version |
| `GET /strategy/versions`, `/strategy/versions/{n}` | any reader | the list, newest first; one version |
| `GET /strategy/diff?from=&to=` | any reader | `{fields: [{key, from, to}], bets: {added, retired, changed}}`, keyed by bet id and status |
| `POST /strategy/bets`, `/strategy/bets/{id}` | the owner only | create; partial update including `rank` and `status`; at most seven active, at most 30 not retired; `rules_out` and `wrong_if` optional; `segment_id`, one of the company's segments or `""` for none ([Segments](#segments)) |
| `POST /market/segments/target` | the owner (a bot, the Librarian too, gets 403) | `{id, target: ours\|none}`: the segment becomes one of the company's or is not, other segments unchanged; `next` is read as `ours`; answers `{id, ours}` |
| `POST /market/segments/remove` | the owner or the Librarian | `{id}`: a segment that is not the company's is retired (its sub-segments move up to its parent; not the root); one that is the company's is taken out of the company by the owner only (`403` for anyone else, as `POST /segments/remove`). Either way its teammate tags go |
| `GET /segments` | any reader | `{segments: [{id, name, who, color, rank, faces, teammates}]}` in list order; `faces` are the dedicated teammates `{ref, name, kind}`, highest on the chart first, at most 24; `teammates` counts them all, hidden people left out |
| `POST /segments` | the owner | `{name, who?, color?}`: a segment the company plays in (a market segment of that name joins, a removed one comes back with its id); answers `{segment}` |
| `POST /segments/order` | the owner | `{ids}`: every segment the company plays in, once each; answers `{segments}` |
| `GET /segments/members` | any reader | `{members: [{member, segments}], truncated}`: every tagged teammate, highest on the chart first, at most 500; `segments` lists the ids and `"all"` |
| `POST /segments/members` | the owner, on their own click | `{member: "bot:<id>" or "person:<id>", segments: [id or "all"]}`, `[]` clears; answers `{member, segments}` |
| `GET /segments/{id}` | any reader | the segment as the list shows it, plus `leads` (its live faces whose manager is not one of them), `strategy` (`{draft, revision, version, fields: [{key, title, text, company, changed, from}]}`; the draft for the owner and the published text otherwise; `from` is `segment` or `company`) and `bets` (`{id, statement, status}`, live ones) |
| `POST /segments/{id}` | the owner | `{name?, who?, color?}`, only what is sent changes; answers `{segment}` |
| `POST /segments/remove` | the owner | `{id}`: takes the segment out of the company's; its teammate tags go; answers `{id, removed}` |
| `PUT /segments/strategy` | the owner | `{id, revision, sections: {summary?, serve?, ...}}`, `""` gives the company's text back; `409` on a stale revision; answers `{strategy}` |
| `GET /market/competitors` | any reader | `{competitors: [{id, name, url}]}`, by name |
| `POST /market/competitors` | the owner or the Librarian | `{name}`: a name or a site; answers `{competitor}` |
| `POST /market/competitors/remove` | the owner or the Librarian | `{id}`: ends its `competes_with` edges; 404 if it is not one |
| `GET /strategy/summary` | any bot or human | plain text: the summary, one line per active bet with its wrong-if, "version N" |
| `POST /strategy/draft-from`, `GET /strategy/draft-from/{job}` | the owner | setup research from `{url?, text?}`: fills the empty sections the pages support, marked unconfirmed, and proposes bets; gives up, reporting what it found, if the owner wrote the draft meanwhile. States are in [Setup](#setup); 422 for a missing or internal address |
| `GET /strategy/guides` | any reader | deprecated: the Default guide only |
| `/lenses/...` | see [Lenses](lenses.md#hub-and-the-api) | list, run, read, add a change to the draft, sharpen |

Ids contain a slash (`segment/screening`), so a segment's path is `/api/v2/segments/segment/screening`. The writes that
name a segment in the body (`POST /segments/remove`, `PUT /segments/strategy`) are not under `/segments/{id}`, so no
segment id can reach them.

A bet's `wrong_if` is `{kind: kpi|thesis|text, kpi_id?, direction?, threshold?, market_id?, text?, check_by}`. The
`strategy_draft` proposal's payload is partial `{summary?, vision?, sections?, bets?: [{id?, statement, why?,
rules_out?, wrong_if?, cites?, status?, segment_id?}], source?, lens?}` (the server sets `source: "lens"` and `lens` for a lens run). Goals take `bet_id` on `POST /goals` and `POST /goals/{id}` and return
it in `GET /goals` and `/goals/tree` ([Goals and KPIs](goals-and-kpis.md#hub-and-the-api)); goal proposals carry it
too. Every change is a `history_events` row (`subject_type` `strategy`), as the market's are.

Related: [Growth](growth.md), [Goals and KPIs](goals-and-kpis.md), [Your Strategy Bot](strategy-bot.md), [Librarian](librarian.md),
[Routines](routines.md), [Agent context](agent-context.md).
