# Finish setup

The first time the owner opens a new environment, the app is a wizard at `#/welcome`: say what the team is building
and confirm its first bets (a strategy draft), name the team, say what it does, build its team chart group by group, add a computer (a Linux Docker runner or a Mac), optionally connect your own
external agent, and press **Create my team**. Every team gets the assistant, BotOps, the Librarian and the Strategy Bot, and none of them can be archived.
**Setup picks the chart, not the bots:** those built-ins are the only bots on day one, and they work at once. Every other role
you picked is laid out as a **Recruiting** seat with its job description and reporting line: no bot, repository, computer slot
or BotOps task until someone picks a bot for it ([A bot's stages](#a-bots-stages)).

The wizard is the first of three pieces, all described below: the wizard, a short tour, and a line under the team list
that points at BotOps. [After the wizard](#after-the-wizard) covers the last two, and the Market tab's empty state, where the
owner asks for market research. To choose a first team and get the most from it, read the [setup guide](onboarding-guide.md).

Nobody else sees the wizard. Only the owner may write it, and the sidebar entry
**Finish setup** appears only while it is unfinished.

While the wizard is open, the sidebar shows only **Finish setup** and the team: the app's pages, pins, Message bots,
**What's new** and the Changelog count in the account menu are hidden. They come back once **Create my team** finishes
(the screen after Create has the full sidebar) and on any other page. On a phone the bottom bar drops Tasks and Updates
during setup. The anonymous usage-count notice is not shown over the wizard or the screen after Create; it appears on the
first other page you open (for example Tasks), and counting starts only once it has been shown ([PRIVACY.md](../PRIVACY.md)).

The first screen also offers **Start with a sample company**, which skips the wizard and starts a made-up five-bot
team instead ([The sample company](sample-company.md)).

**Unreleased:** When the **AI providers** step appears, its main choice comes before the sample-company and
**Company YAML file** options. Provider checkboxes, **Via OpenRouter** and **Default model** appear only when you
choose **Your company's plans or API keys**.

If you already chose a team on the public setup page, the first screen also accepts its **Company YAML file**.
Choose the file, check the preview (the built-ins that work from day one, then every other role marked **Recruiting**),
then press **Use this team**. Tico lays out exactly that chart: each role is a Recruiting seat with the file's title,
a job description from its template card (what it owns, and the file's reason for the role) and its reporting line,
a role under a role included (`seat:<id>`). Only the built-ins become bots, once each. Import retries do not duplicate
a seat, and an import cannot replace a company whose setup is complete. A file named at install
(`TICO_COMPANY_FILE_B64` or `TICO_COMPANY_FILE`) is imported when the server starts; if it cannot be read or applied,
the server still starts, **no sample team is set up in its place**, and the owner sees why on the welcome page and on
Health ("Company file from setup not imported") until it is over: an import works (the same file again included), the
team is set up on the welcome page, the server starts with the file fixed back to the one already applied or with no
company file at all, or the owner presses **Dismiss** on the welcome page. A server start with the same broken file
shows it again.

**Unreleased:** A role's optional `team` (the name of its group) puts it in a group of that name: one group per name, made
from the file, or the group that already has it. Without one, a role goes in its template's group.

**Unreleased:** A file from the setup page's Strategy step (format 0.3, with a `strategy` block) also brings the strategy
it drafted, for the owner to confirm; nothing is published. The preview lists it under `strategy`. The import writes the
[strategy draft](strategy.md#setup): the summary and vision, and the four sections marked
`_Unconfirmed: drafted from the company file._` (only where empty), with the file's listening requests as a
**Watching** section; each bet `proposed`, its wrong-if a text test due on the file's check date (90 days out when it
has none); and each goal on the bet it came from. With a market not defined yet it adds the
[market by spend](librarian.md#market-spend): the definition and its segments, each sized segment's size `proposed`
on its own evidence (a segment with none stays unsized), the company in its `ours` segment, and each player as a company
on the market graph with its site a watched page. The posts the page found are filed as new market insights, so the
Librarian's next curate sees them. A part the app refuses (a name the market graph already has, say) is left out and
named on the setup record (`company_import.strategy.skipped`). A format 0.2 file imports exactly as before.

This starter import supports one owner and catalog AI employees with the default rules. The signed-in owner remains
the owner; a file does not add sign-in accounts. A person the file names who holds a role has no seat or bot for it:
they are kept on the setup record to invite, and a role under them reports to the owner until they are invited
(Settings > People > **From your setup**: type their email and press **Invite**, which adds them to the roster, moves
the seats waiting on them under them and sends the invite, see [People](people.md#invites)). Other coworkers, existing
external agents and custom rules are refused with an explanation. The file's goals are imported onto
[Goals](goals-and-kpis.md): the owner's goals, goals the file gives a built-in bot such as the Assistant (Goals keeps the
built-ins out of its tree) and goals of roles that are seats (a seat holds no work) become company goals, each with the
goal's measure as a KPI carrying the file's baseline and target (the file holds no readings, so a new KPI starts with
no history). A file holds up to 325 goals: the company's 3, 2 for each department and 1 for each of up to 300 roles,
which covers every goal the setup page suggests; a file with more is refused before anything is created. First jobs,
routines, model choices and tool connections are saved for later setup and listed in the preview; the team uses its
currently configured AI providers.

The implementation is `backend/onboarding.py` (the record, creating the team), `ui/setup-strategy.js` (What are you
building and Your bets, over `backend/strategy_setup.py`), `backend/recruit.py` and `backend/recruit_rank.py`
(the team builder's templates and suggestions), `ui/org-builder.js` (the team chart screen), `ui/first-run.js` (who reports to whom, the review
and the screen after Create), Tico HQ's `POST /v1/recruit` ([its API](tico-hq.md), for HQ's suggestions), `clients/catalog.py` (turning a template into a repository),
`runner/catalog_job.py` (the bots the computer sets up itself, run by the runner as `python -m runner catalog`) and `templates/catalog/` (the templates). The quick start in the
[README](../README.md) is the same flow with the commands in it.

## The screens

Every **Next** saves the whole draft with `PUT /api/v2/setup`, so a closed tab loses nothing.

| Screen | What it asks | What it stores |
|---|---|---|
| AI providers | Only while no provider is chosen yet; otherwise the wizard starts at What are you building. Choose [how bots get their models](ai-gateway.md#choose-how-bots-get-their-models). To choose later, select **Your company's plans or API keys**, untick its providers and press **Skip for now** | The team's providers, as Settings > Models saves them |
| What are you building | **Website** (optional) and **What you're building**: the team's one line, or a new idea in a few sentences. **Next** with either starts the strategy research (`POST /api/v2/strategy/draft-from`); an address that is not a public website keeps you on the step with the reason. To retry interrupted research, see [Recover from interrupted strategy research](#recover-from-interrupted-strategy-research) | `names.website` and `answers.building`. The website is stored as an `https://` address (a bare `acme.example` gets the scheme; anything that is not a public web address is refused with 422); finishing setup files the Librarian's market task from it |
| Your bets | The research's bets to **Keep**, **Edit** or **Strike** (Strike retires one), **Add** for your own, and **What we won't do**; the draft (Who we serve, What we sell, kept bets, won't do) fills in beside them, below them on a phone. While the research runs the step polls `GET /api/v2/strategy/draft-from/latest`. Without a model provider, or when the research failed, it is the same fields as a plain form; when it gave up because the draft changed meanwhile, it lists what it found, each with **Add**. **Next** waits for one active bet with Rules out and Wrong if, and a line under What we won't do ([Strategy](strategy.md#setup)) | The strategy **draft** and its bets (`/api/v2/strategy`). Nothing is published |
| Names | **Team/Company name**, app name, and optionally your own name | `names`. From the moment they are saved they override `TICO_TEAM_NAME` (the old `TICO_COMPANY_NAME` remains an alias) and `TICO_APP_NAME` everywhere, including in the template cards. **Your name** (`names.owner_name`) is saved on the owner's roster entry, so the team chart and the sidebar show it instead of the address; it is prefilled from the roster, or from the display name the sign-in proxy vouches for (a `name` claim from Cloudflare Access or the AWS load balancer), and left blank it changes nothing. **Team email domain** (`names.team_domain`) is asked only when the owner signs in with public email such as gmail.com and no team address is on the roster; it is optional, and lets members add teammates at that domain (it does not let anyone sign in). The wizard does not ask for an assistant name: the tab is always called Assistant, and `names.assistant_name` is `TICO_ASSISTANT_NAME`, which is `Assistant` unless set (a name equal to the team's reads as `Assistant`) |
| About the team | What you do (prefilled from What you're building), who you sell to, whether software is your product, and team size | `answers`. Whether software is the product decides which groups start picked, and the description helps the suggestions. It is also written into every bot's `knowledge/company.md` |
| Your team chart | The groups, then one question per group and the bots to recruit into it, with the chart growing beside it, and the message bot switch under the finished chart ([The team builder](#the-team-builder)) | `answers.departments`, `answers.briefings` and `selected`: for each chosen slug, its template, display name, the `AGENT.md` text and `reports_to` (a human `human:<id>` or a bot slug). Nothing is created yet |
| Add the computer that runs your bots | If a runner is already online (the server's own) the step is one line, `<label> online`. Otherwise the kind of computer, **A Linux or cloud server (Docker)** when the server itself runs in Docker (`in_docker` in `GET /api/v2/config`, the usual install) and **A Mac** when it does not, then a one-time code or setup file and the commands | Nothing. It polls `GET /api/v2/setup` every ten seconds and reports the enrolled computer |
| Connect an external agent | Optional: **Connect an external agent** makes a personal token and the MCP setup to paste into Grok, Dots, Muse or any MCP agent ([Connect an external agent](connect-an-agent.md)) | Nothing in the record; the token is the owner's own (`POST /api/v2/me/tokens`) |
| Review and create | A summary of all of it, including the **AI providers** choice (Unreleased), and the team with each bot's reports-to | **Create my team** calls `POST /api/v2/setup/complete` |
| After Create | One screen, below | Nothing in the record |

**Unreleased:** The Computer step's model sign-in commands match your provider choice. Both subscription choices show
Claude and **ChatGPT through Codex**; **Tico Gateway only** shows no model sign-in commands. With
**Your company's plans or API keys**, commands appear only for enabled providers. Each tool must support sign-in on that kind of computer.

### The answers

| Field | Shape |
|---|---|
| `building` | What the team is building: the owner's one line, or a new idea in a few sentences, up to 4000 characters. With `names.website` it is what the strategy research reads ([Strategy](strategy.md#setup)) and what the Librarian's market task names; the org builder reads it when `what_we_do` is empty |
| `what_we_do` | Free text, up to 2000 characters |
| `customers` | `businesses`, `consumers`, `both`, or empty |
| `software_product` | `yes`, `no`, or empty: whether software is the product |
| `team_size` | Free text, a choice from the wizard's list. Shown to a human; nothing reads it |
| `departments` | The team builder's groups, in order: any of `sales`, `marketing`, `support`, `finance`, `operations`, `legal`, `hr`, `product`, `engineering`. A skipped one is left out |
| `briefings` | The one-line answer for each group, at most 500 characters, keyed by group. BotOps's setup tasks carry them as "`<department>` today: …" |
| `work_arrives`, `repetitive_work`, `pains`, `pains_text`, `tools` | Earlier questions. The wizard no longer asks them: "What hurts, and what you use" is gone, so there are no pain chips, no "in your own words" box and no tool checkboxes. An older record keeps them and a custom client may still send them; Tico accepts them and reads none |

<a id="the-org-builder"></a>

### The team builder

The third screen builds the team chart one group at a time, with the chart drawn beside it and growing as bots are
checked (on a phone the chart is a strip above the card that opens to the whole chart). It replaces the old starter team and full team
chart: there is no fixed team any more.

1. **Groups.** "What groups do you want?" as tiles: Sales, Marketing, Customer Support, Finance, Operations, Legal, HR,
   Product and Engineering (`templates/groups.yaml`). Sales, Marketing, Customer Support, Finance and Operations start picked;
   Product and Engineering (`software_only`) start picked only when software is the product. Pick any.
2. **One group at a time.** A card with the group's icon, a one-line description and goal, its one question and a single-line
   answer ("What kind of sales do you do today?"). **Recruit bots** (or Enter) shows "Recruiting bots…" for a moment, then the suggested
   bots as checkable cards: icon, name, the card's summary and a "why" line. The group head is checked, so are the `default` cards
   and anything the suggestion says to add; `common` cards are shown unchecked; the rest of the group's cards are under **More**, one
   full-width button (keyboard operable) that stays open or shut through a redraw.
   **Back** and **Skip group** are always there. Each group answered or skipped saves the draft.
3. **The chart.** The owner at the top (CEO), each group hanging off one line and its bots under it, the head first. Each bot wears
   the blob avatar it will have, with its template's icon. A group not yet reached is dashed; a skipped one says so. A bot animates
   in once, when it is first checked. Built-in bots and the message bot are not on the chart.
4. **Finish.** The finished chart, "5 groups · 11 bots". Click a bot to rename it, point it at another human or bot, or remove it;
   click a group to go back to it. Below it, **Message bots** has one switch per message bot card, off by default;
   switched on, it asks whose mailbox the bot reads. **Next** continues to the computer.

Each group head reports to the owner, and every other bot to its group's head while the head is on the chart (to the owner
otherwise). A message bot reports to the owner and is never offered as a manager. `selected` is sent in the order to set the bots up: the
built-ins, each group's head and its bots, then message bots. A worker the human re-points keeps its new manager; two bots that would
report to each other are refused on the screen.

**Where a card sits.** A card's `group` (one of the nine ids). A card of `kind: helper` and a card whose group the builder does
not offer (the Leadership extra) are in none. A card with no `group` sits in the group whose `head` it is, or that lists it in its
head's `team_templates`, or its `pack`'s (`basics` is Operations). A group's head is its card with `lead: true`, else the `head`
`templates/groups.yaml` names. `icon` is a Material Symbols name, drawn from the app's own icon font
(`scripts/build-icon-font.py` reads every card's and group's `icon`); a card without one takes its group's. `suggest` is
`default`, `common` (the default) or `niche`. `tags` are the words the recommender matches; a card without tags uses its `pains`.

### Suggestions: Tico HQ, or the local recommender

The browser only ever talks to its own server:

| Route | What it answers |
|---|---|
| `GET /api/v2/setup/groups` | `{version, departments: [{id, name, description, goal, question, placeholder, icon, head, software_only}], cards: [{template, name, department, icon, tags, suggest, summary, lead, business_only}], hq: {available, off_by}}` |
| `POST /api/v2/setup/recruit` | Body `{department, briefing, share}` (`briefing` at most 500 characters). Answers `{bots: [{template_id, why}], suggested_default: [template_id], bet: {id, statement} \| null, source: "hq" \| "local", shared, off_by}` |

Both are for the owner and bot administrators, and neither is part of the stable v2 contract (like the rest of the wizard's routes).

The team builder has one **Suggestions from Tico HQ** toggle, and its setting covers every group you answer. It comes with a privacy link and the exact payload: group, answer,
team description, customer type, software choice, catalog version and install ID (when counting is on and its notice has been shown). It is on by default and off (and disabled, with the
reason) when the install may not ask HQ. The server asks Tico HQ (`POST <TICO_HQ_URL>/v1/recruit`, [Tico HQ](tico-hq.md)) only when the
toggle is on **and** none of these is true: demo mode, `TICO_REHEARSAL=1`, `TICO_TELEMETRY=off`, `DO_NOT_TRACK` set to any value other than empty, `0`, `false` or `no`, or the anonymous usage count switched off
in Settings (`hq.off_by` says which). It sends the group, the answer, three facts from "About the team" (`what`, at most 500
characters; `sells_to`; `software`), the template version and, while the usage count is on and its notice has been shown, its install id; it waits at most 6 seconds and
keeps only template ids that are this group's in its own templates. HQ answers template ids and a short why, never text a bot would
follow. What is sent and kept is in [PRIVACY.md](../PRIVACY.md).

Otherwise, or on any failure, the **local recommender** (`backend/recruit_rank.py`) answers, with no network: the head first, then the
cards the answer matches (a tag, or two words of a longer tag phrase; a word of the name; two or more words of the summary; the answer
counts double "What you do", and the group's own name counts for nothing), then `default` and `common` cards; a `niche` card only when the answer names it; a business-only card last for a team that sells
only to consumers. The why is "Heads Sales and reports to you" for the head; a short tag of two or more words the answer writes as a
phrase, in order and side by side ("Matches “flaky tests”"; not for "flaky deploys but reliable tests"), unless most of the group's cards carry those words; else "A starting point for Sales" or "Common in Sales",
and no why for a `niche` card the answer brought in on single words. A single word, a word of a name or two words of a summary only
rank: shown as a why they read as keyword matching. When Tico HQ's answer still says "Matches “<word>”", the install's own why replaces
it. The same answer always gives the same list.

The strategy draft's bets ([Strategy](strategy.md)) count like "What you do" in the local answer (a bet on price brings in the
pricing cards), and `bet` names the bet the group clearly serves: the one sharing at least two words with the group's description,
goal and question, its cards' names and tags and the owner's answer, and more than any other bet. Bets never go to Tico HQ; with no
bets, `bet` is null and the answer is as before.

### What Create does

**Create my team** (`POST /api/v2/setup/complete`) writes to the server only, in one request (a chart of 25 bots takes a fraction of a
second). It reaches no computer.

- BotOps, the assistant, the Librarian and the Strategy Bot, so they exist before anything is addressed to them, each `planned`
  with the card's summary as its description, `bot-<slug>` as its repository, the card's runtime, model and reasoning effort, the human
  it reports to, and the owner as its owner. A model a deployment does not offer falls back to the product default. They are
  Active on day one.
- **Everything else picked is a Recruiting seat** (`seats` on the record: `{pick: seat id}`), with the reviewed name as its
  title, a job description from the card and the reviewed instructions, the reporting line picked (a pick under another pick
  reports to that seat) and the pick's group. No bot, repository, computer slot or BotOps task exists for it. The bullets
  below about starters and other templates describe a bot made from a template: the sample company's, and from a seat once
  someone picks a bot for it.
- **No AI provider and no computer are needed.** The team is created first. With no provider chosen a bot names no runtime or model and
  follows the team default, so it runs once a provider is added in Settings > Models. With no computer enrolled the bots stay
  `planned`; when the owner's computer enrolls, the bots are placed on it and activated. Until then the after-Create screen says
  "Waiting for a computer" or "Add an AI provider".
- Each bot goes in the **group** its template belongs to (Marketing, Product, Engineering ...); the group is made if the team has none yet,
  and one it already has by that name is reused ([the team chart](org-chart.md#groups)). Built-in bots stay outside groups. A group
  made here that clearly serves one of the strategy draft's bets (the org builder's `bet`) stores it as its `bet_id`.
- With a website (`names.website`), the Librarian's "Set up the market map" is filed now, once, from the website and `building`,
  unless the owner already filed it from the Market page ([An empty Market page](#an-empty-market-page)).
- The template and the reviewed instructions are stored in the bot's server-side config, with the **template version** (the release whose
  templates it came from: `template_version`).
- A **starter** template (a card with a `first_routine` and an `onboarding` conversation) is created whole and parked:
  - `onboarding_state: needs_setup`, exposed on `/api/v2/bots`, `/api/v2/bots/{bot}` and `/api/v2/org`;
  - its first routine is seeded off (the template declares `enabled: false`) and goes on when its setup starts (**Set up**, a first message from someone who manages it, or go-live), so nobody approves it separately;
  - **no task is filed for BotOps**: the computer materializes its repository from its template as soon as the bot is placed on it
    (`materialize: true` in its config, which only the wizard writes), and the bot is activated as soon as it is placed;
  - the scheduler skips it, and only a human's chat message is claimed for it (a task message, a Slack route, a bot's request or a routine
    waits), so nothing runs until it is set up;
  - it does not count toward a member's bot limit while parked.
  The built-ins and every other template are not parked.
- Any other template is created `planned` and gets one task for BotOps, as before. Title: `Set up <Display> from the <template> template`.
  Body: the slug, the template, the display name, the reviewed instructions in a fenced block, and the answers.
- Every bot from a template that no computer holds is assigned to the owner's most recently enrolled computer. If none is enrolled yet,
  enrolling the owner's computer afterwards does the same, so the order the two are done in does not matter. A bot someone placed by hand is
  never moved.
- Finishing twice is safe: it reopens no task, creates no second bot or seat, and keeps the first completion time.
- With nothing picked and no company file (signing up directly), finishing makes only the built-ins: the chart is empty, to
  recruit into.

### A bot's stages

Every bot goes **Recruiting -> Onboarding -> Active** (`backend/lifecycle.py`).

- **Recruiting** is a seat: a role on the org chart with its job description and reporting line, and no bot, repository or
  computer slot (`hub seat create|list|update|close`, `/api/v2/seats`, and `seats` on `GET /api/v2/org`). It holds no work:
  a task for it is refused with the name of the manager it reports to, it is never woken and it has no routines. The owner
  places a seat anywhere; anyone else under themselves or someone below them.
- A seat is filled by recruiting it ([Recruiting a seat](#recruiting-a-seat)); the bot it gets starts Onboarding.
- **A cloud agent** Tico does not connect yet is a request, not a bot: `POST /api/v2/cloud-agents/{id}/request` (or
  `POST /api/v2/seats/{id}/request-agent` for a seat that has none) makes a seat with `agent` and `request_state`, and
  files the request with Tico's team through support as a feedback ticket carrying only its message, with no install ID.
  The seat and its first task are made first and the seat is `filing` until Tico's ticket is kept, then `requested`. A
  repeated key, or an identical request sent at the same time, shares that one seat and ticket. A first task whose title
  matches an open task is refused (`409`, `duplicate`) before Tico hears of it. The first task waits on the seat as a task
  its person owns; `GET /api/v2/seats/{id}/request` shows it to anyone who may read it. While a request is filing, being
  cancelled, or open, the seat cannot be closed, filled, hired, or renamed, moved or given another team (`409`).
  **Cancel request** (`POST /api/v2/seats/{id}/cancel-request`) marks the seat `cancelling`, tells Tico on the same
  ticket, closes the seat and lets go of the first task: it is declined if the person may update it, else it stays with
  its owner. If Tico cannot be reached, or the last step fails, the seat goes back to `requested` and can be tried again.
  A request a stopped process left `filing` or `cancelling` is recovered by the next request or cancel on its seat once it
  is older than two minutes, and every such request is recovered when the store starts. A request for a seat in a team
  needs the right to add to that team (`403`), as an add does.
- **Claude Code and Codex** are added, not requested: `POST /api/v2/cloud-agents/{claude|codex}/add` with `{seat_id}`
  fills the seat with a bot on that harness, owned by the person. Its role is the template Recruit ranks first for the
  seat, so it lands exactly as a hire of that template does: in Onboarding, in the seat's place on the chart, with its
  repository built by its computer (a starter) or by BotOps (any other template). It joins the seat's team and runs under
  its own bot identity, with no personal token issued. An add with `{team, reports_to}` and no seat first opens a
  *Software engineer* seat there and names the bot for the agent. A seat no template fits is refused (`409`) and leaves
  nothing behind: a bot needs a role to build its repository (`backend/cloud_agents.py`).
- **Onboarding** replaces *Set up*. A starter is created Onboarding: the team chart shows a small amber gear (hover it for
  *Onboarding*) and its page an **Onboarding** pill. **Start onboarding** sends it "Let's set you up." as the person, in their
  own chat with it (the **Chat** tab); any first message from a person does the same. It learns only: it answers and asks in
  chat, reads, connects tools and talks with its manager. It takes no task, files none, runs no routine (timed or on an event),
  comments on, asks about or attaches to no task (one it held before included), asks no approval, sends no email and posts
  nothing on Slack, and sends messages, answers and notes only to its manager, the people above it and a person it answers in
  chat. A run that cannot confirm its stage with Tico sends no email or Slack post either. Each attempt is refused with a
  plain reason. Any number of bots may be onboarding at once.
- When it has learned enough, the bot says so with `hub bot ready --summary "..."` (MCP `hub_bot_ready`); its page shows the
  summary. Only a person above it on the org chart (its manager, their managers, the owner) sees **Mark onboarding complete**
  (`hub bot setup-done <bot>`, `POST /api/v2/bots/{bot}/onboarded`, checked on the server; the bot's own call is its "I'm
  ready"). That makes it **Active**: its routines run, its queued work starts and it counts toward a member's limit.

`stage` on a bot (`GET /api/v2/bots`, `/api/v2/bots/{bot}`, `/api/v2/org`) is `onboarding` or `active`; while onboarding it also
carries `can_complete_onboarding` and `onboarding_ready`. On an upgrade nothing is rewritten: a bot in the old *Set up* stage
(`onboarding_state` `needs_setup` or `needs_onboarding`) shows as Onboarding, a `planned` bot without a computer keeps working as
before, and no Active bot changes stage.

### Re-org suggestions

Ask the Strategy Bot to compare the company chart with one of Tico's published starter charts. It uses the bundled public models
only; its suggestions do not read another company's bots or data. The Strategy Bot can propose adding Recruiting seats as one
confirmation card. No seat is added until the owner confirms, and the server checks the owner's authority when it applies the
whole change. Members can add seats under themselves or someone below them; only the owner can add seats elsewhere.

### After Create: one screen

- **Your team is created**: the heading, then the one next step as the only primary button. With no computer yet it says
  *Waiting for a computer* once and offers **Add a computer** (Settings > Computers); with a computer but no AI provider,
  **Add an AI provider**; otherwise **Open Tasks** at the bottom. **Show me around** starts the tour.
- The Recruiting seats are on the sidebar's team chart, each marked **Recruiting** where its reporting line puts it.
- Each bot, in the team chart's groups and order (bots outside any group first, the built-ins last), with one status:
  *Waiting* while the team waits on a computer or provider, *Setting up* until a starter's repository exists, then its
  **Set up** button; *Active*; or a link to the setup task BotOps is working.
- **AI providers**: the first link opens Settings > Models so bots can start working.
- **Strategy: a draft. Publish your strategy**: a link to Strategy, until a version is published (Getting started's
  `strategy` step).
- **Add an admin**: a name and an email. The human joins the roster and the sign-in list and is made an admin (`POST /api/v2/access/humans`,
  then `POST /api/v2/access/humans/{id}` with `role: admin`). Tico sends no email. On a local install, only you can sign in
  until you add a domain and sign-in; adding an admin still saves their roster entry.
- **Who owns each bot**: add a human as an owner of any bot (`POST /api/v2/bots/{bot}/co-owners`; [permissions](permissions.md#bot-owners)).
- **Tools**: links to Credentials and Tools, where credentials go. Credentials
  are entered in those fields, **never in a chat with a bot**; the BotOps playbook says the same, and a credential pasted into a chat is treated as
  leaked.

## After the wizard

The wizard is done once, by the owner. Everything after it is per human, so a teammate who
joins later gets the same help without the owner doing anything.

```
wizard  ->  tour (once)  ->  "Ask to add or edit your bots" line
             replay: account  until a bot of your own exists
```

### The tour

Six spotlight steps: Updates, Tasks, your bots (the team list), Docs, Strategy, Meetings. **Next** moves
on, **Skip** or **Esc** closes it, and focus stays inside it. On a phone it opens the navigation drawer
and shows the same steps. It never opens over the wizard: the owner starts it with **Show me around** on the screen after Create, or it opens
by itself once on their next visit (anyone else: their first visit). It can be replayed
from the account menu with **Take the tour**. That it was seen is kept per human.

### Bots

There is no checklist, no card above any page and no card in the sidebar. While there are no bots of your own (the Create your
first bot step is not done), one line of muted text sits under the team list: "Ask to add or edit your bots",
with **Ask** opening the Assistant panel over the page. It shows only to humans who may add bots (the owner and bot administrators,
`can_build`), and has nothing to close. Asking the Assistant (it hands the request to BotOps) is how a bot is added (`playbooks/build-me-a-bot.md`); connecting
an external agent you already have is the **Connect an external agent** button in the sidebar footer.

### An empty Market page

Market is the Market tab of [Strategy](strategy.md) (`#/strategy/market`; `#/market` redirects there). While the market has
no entity and no page someone wrote (the seed's pages do not count), the tab is an empty state
instead of the graph, the index and the ask box. The owner sees the Librarian's four questions, one at a time (what you sell,
who buys it, who you compete with, what to watch), with **Skip for now** and an **Attach files** link to the Docs import.
Answers are kept as given, so a skipped setup shows **Continue** at the same question next time
([librarian.md](librarian.md#first-run-setup)). Everyone else sees "Nothing here yet."

The last question's **Start research** calls `POST /api/v2/librarian/setup/market` with `action: finish`, which files one task,
"Set up the market map", to the Librarian; `409 librarian` while the Librarian is not running. (`POST
/api/v2/setup/getting-started/market` `{"text"}` still files the same task from one box of text.) The Librarian's
`playbooks/market-setup.md` researches the answers and writes the market pages and graph ([librarian.md](librarian.md)). The page
then shows "The Librarian is researching your market. This usually takes 5–10 minutes." with a link to the task, until the market
has content (at least two minutes, at most thirty), kept in the browser, polling the market every 30 seconds. Then the normal
Market page is drawn. The code is `ui/market-page.js`. When setup was given the team's website (`names.website`), finishing setup files "Set up the
market map" from it and the market page opens on the researching notice; the Librarian reads that website from `hub strategy show`
when the task has none, and the four questions stay for a team that gave no website.

### What is stored, and who may do what

A human's choices are one row in `preferences` (key `onboarding.progress`, the same per-human store as
`/api/v2/preferences/{key}`): whether they saw the tour.
`POST /api/v2/setup/getting-started/state` writes only the caller's own row, and the read shows only
the caller's own choices. Runners and bots get `403`.

| Endpoint | Who |
|---|---|
| `GET /api/v2/setup/getting-started` | Any human (the BotOps line and the tour read it) |
| `POST /api/v2/setup/getting-started/state` | Any human, for themselves |
| `POST /api/v2/setup/getting-started/market` | Owner |

The code is `backend/getting_started.py` and `ui/getting-started.js`. Tests: `backend/tests/test_getting_started.py`
and `ui/tests/getting-started.cjs`.

## What finishing creates

See [What Create does](#what-create-does). Nothing there reaches a computer or a repository: it writes definitions and tasks, and the
computer does the rest.

## What the computer does

The runner materializes the bootstrap bots and the starters the wizard created during readiness, once per bot, when the
repository is missing and this computer is the one the bot is assigned to. That is a few seconds after Create, so the screen after it
shows *setting up* per bot until each repository exists (about 70 ms a bot; 25 bots take under two seconds). Everything else stays missing
until BotOps has built it.

1. Read the cards from this checkout's `templates/catalog/`.
2. Take the template from the bot's server-side config, and use it only if its card says
   `bootstrap: true` or the bot's config says `materialize: true` (only the wizard writes that). A registration made before templates existed carries no template, so a bot
   named `coo` falls back to the assistant template and one named `botops` to the botops template.
3. Read the names from `GET /api/v2/config` and the answers from `GET /api/v2/setup` with
   this computer's own credential. Both are read fresh, because the wizard is answered after the
   computer is enrolled.
4. Copy the template folder to `<workspace>/bot-<slug>`, fill the placeholders, set `name:` in
   `bot.yaml` to the slug, write `knowledge/company.md` from the answers, replace `AGENT.md`
   with the reviewed instructions when there are any, then `git init` and one commit.

An existing directory is never touched. A failure is a readiness problem on that bot rather than an
exception, so the computer keeps reporting the others.

## What BotOps does

BotOps works one setup task at a time, following `playbooks/set-up-a-bot.md` in its own repository:
read the task, `hub bot create --template <template> --name "<Display>"` (Tico picks the slug from the name), put the reviewed
instructions into `AGENT.md` (or tailor the template's to the answers), run `hub bot check <slug>`
and fix every failure, then commit the result.

For a human's authorized chat request, BotOps finishes the work: it places the bot on a computer,
turns it on, starts Setup, opens [Credential cards](credential-vault.md#credentials-asked-for-in-the-chat)
for missing values, runs one small test and reports the result in chat. It stores and grants
Credentials with the requester's rights; values stay out of messages.

An unattended setup task prepares and checks the repository, then reports readiness, the repository
path and any missing Tool or Credential to the owner. It leaves activation to the owner in that case.
An existing repository is checked and updated for the requested work, rather than overwritten.

## The assistant, BotOps, the Librarian and the Strategy Bot are built in

Every team gets all four, and none is a choice: their cards are `required: true` in the wizard, so the wizard
builds them whatever else is ticked, and they become active once a computer is enrolled ([Activating](#activating)).
The assistant is every human's private [Assistant](assistant.md) (its own page, **Assistant** in the left rail); it also works in the
background: it routes Slack messages to the bot that owns them, takes meetings and tasks nobody was named for, reviews
BotOps' refused writes, and runs its own routines.

None can be archived or deleted by anyone, the owner included, through Settings, the API, `hub` or BotOps itself:
the archive route answers `409 system_bot`. Pausing, renaming and editing their instructions stay allowed. Settings >
Bots lists them as **Built-in**, with no Archive or Delete control. Like every bot they start open to everyone; their
**Access** can be narrowed in Settings > Bots ([permissions](permissions.md)), and the owner keeps full access to them
whatever it says.

The third, the [Librarian](librarian.md#built-in), answers questions from the team's docs. A team from before it existed gets it on
update, without a click, once a model is chosen and a computer is enrolled; until then its owner gets **Turn on the Librarian** in Ask the Librarian.

The fourth, the [Strategy Bot](goals-and-kpis.md#the-strategy-bot), keeps the KPIs and sets goals' automatic colours. It is
built the same way (a required, bootstrap card; a team from before it existed gets it on update once a model is chosen
and a computer is enrolled). Its routines start paused: the server arms the daily KPI pass once the first KPI exists, and the
weekly goals review (the Monday note) is started too, since every install starts at **Suggests actions**; choosing
**Passive** pauses it ([Your Strategy Bot](strategy-bot.md)).

A team that set the assistant aside before it was built in (v0.2.1 to v0.2.9 let the wizard skip it) keeps it archived
on update: nothing restores it automatically. Its owner sees "The Assistant is off" on the Assistant page and at the top of
Settings > General, and one click on **Turn on** brings the same bot back with its history (or adds it from its
template if it is missing), places it on BotOps' computer and activates it. From then on it cannot be archived again.
While an assistant is off, or paused, what used to fall back to it goes to BotOps or to a human:

| what | with no assistant |
|---|---|
| Slack, nobody at threshold, or every chosen bot refused | BotOps gets the message with the top three candidates and asks which bot; with no BotOps running either, the message is recorded and nobody is woken |
| Slack, no decisions key | the same fallback bot (BotOps) |
| Meetings and notes sent in **Auto**, for a human with no bot of their own | BotOps, which takes work from anyone the way the assistant does |
| A human handing work to "whoever takes it" | BotOps accepts it from anyone, as the assistant does |
| Review of BotOps' refused writes (rule 8) | a task for the owner, which shows in Needs you |
| A bot whose `reports_to` names a bot that is not there | shown under the owner in the team chart, not hidden |

## Activating

Only an active bot is given work. Finishing setup activates the assistant and BotOps as soon
as a computer hosts them (and again when a computer is enrolled later), because BotOps cannot be
handed setup tasks while it is planned. A starter is activated the same way once placed, and is still parked: it answers a human and
nothing else until it is set up. A ready bot has an **Activate** action when its repository is
reported present. BotOps can activate it for an authorized human chat request; an unattended
setup task reports readiness for the owner to act.

Automatic placement when a Computer enrolls skips only archived teammates. Paused or draining teammates
receive an assignment too; their pause or drain still holds their work. Legacy configurations without a teammate
state are also placed. Existing assignments and repository grants stay in place.

## Adding a bot later

**Team → + → Post a job** (or a group's **+**) makes a Recruiting seat first: a job title, who it reports to (you, or for
the owner anyone on the chart; otherwise a bot you manage), its team and a job description. Then it opens the seat's
Applicants page ([Recruiting a seat](#recruiting-a-seat)).

**Settings → Bots → Add bot** (Tico Recruit) starts with a role title and job description, then offers **Post a job**
(the same Recruiting seat), a template, an existing agent or a bot from scratch. The optional tools and
reviewer answers carry into the selected template's instructions. Choosing a template there uses `POST /api/v2/bots`
and creates a planned bot that waits for its setup conversation. External services link to their own setup instructions.

### Recruiting a seat

Open a seat from its row on the team chart (`#/seat/<id>`) or right after Post a job makes it.
`backend/recruiting.py` does the work; the pages are `ui/app/recruit.js`.

- **The job description.** Leaving the title asks `POST /api/v2/seats/draft` (`{title, team, reports_to}`), which answers
  and stores nothing: the catalog template that is the job the title names (its name shares a word of the work with
  the title, never only a word of rank or a kind of job such as "Engineer" or "Analyst"; a title ending in a kind of job wants
  that kind, and a head's title the department's head), composed at once into one line
  of summary, "What you'll do" from what it owns and "Works in" from its tools (`source: "catalog"`). For a title no
  template is, such as "Rust engineer", the team's model writes one from the title within five seconds (`source:
  "model"`), or nothing comes back without a model (`source: "none"`). **Redraft** (`redraft: true`) also asks the team's model for one rewrite, given five seconds; if
  there is no model or it fails, the composed text comes back. A description typed by hand is never replaced by the first
  draft. Anyone who may change the seat edits it later from **Job post** (`PATCH /api/v2/seats/{id}`). Reading is
  `GET /api/v2/seats/{id}`, with `description_by` (`chart` or `person`) and `can_edit`.
- **Applicants** (`POST /api/v2/seats/{id}/recruit`) answers at once, on this Tico, with no outside service and no trial
  task: up to eight Tico catalog templates matched against the title and description. Each has `match`: `strong` when
  the template's name shares a word of the work with the title (not "Engineer" alone), `good` when it fits at all, else empty; strong ones come
  first. Each carries a one-line summary, tags, what it owns and never does, its onboarding questions, what it needs (each
  `connected`, `on_hire` or `not_set`) and the model it suggests among the providers the team has turned on, with the list
  of those models to choose another. Built-in bots (BotOps, the Strategy Bot, the message bot) are never applicants. Each
  applicant has its own page (`#/seat/<id>/<template>`) with a **Hire** box: name, reports to, computer and model.
- **Hire** (`POST /api/v2/seats/{id}/hire`, `{template, model, name?, runner_id?}`) creates the bot through the same checks
  as any new bot: named as given (the seat's title by default), in the seat's place on the chart (`reports_to`) and
  group, owned by the person who hired, with the template's instructions plus the job description, and **Onboarding**. The
  seat is then filled and leaves the chart; open seats that reported to it move under the new bot. Only then is its
  repository made: a starter template's by the computer (`materialize`), any other's by a BotOps "Set up" task. A filled
  seat, a template that is not an applicant, a name already taken or a model the team has not turned on is refused and
  nothing is made.
- Who may post and hire: the owner for any seat; a person for a seat under themselves or someone below them. Bots read
  seats but do not hire.

The separate [LinkedBots directory](https://linkedbots.ai/) has a playful feed and a small Tico Recruit workspace at [recruit.tico.dev](https://recruit.tico.dev/).
It can hand a selected template and role brief into your Tico using a URL fragment; it does not authenticate you or
create a bot automatically. Sign in, review the role and explicitly choose Create. The provider's magic-link editing
session on that site grants no access to your Tico. Older installs can select the named template manually.

- **Settings → Bots → Add from template.** The same cards, minus the bots that already exist. A starter is created parked, exactly as Create
  makes it; any other template is created `planned` with the same BotOps task the wizard would have filed.
- **From a BotOps task**, inside a run: `hub bot create --template <template> --name
  "<Display>"`, then `hub bot check <slug>` with the slug it printed. Both need `HUB_WORKSPACE`, which the runner puts in
  every run's environment.
- **An agent you already run elsewhere**: a [Hermes](hermes-agents.md) or [OpenClaw](openclaw-agents.md) profile
  becomes a bot with a pairing code, no template; see [Connect an external agent](connect-an-agent.md#an-agent-that-is-a-bot-not-you).
- **By hand on the Mac**: `scripts/tico -e <env> bot create <slug> --template <template>`. This
  materializes from the same templates with the environment's names but without the wizard
  answers, so `knowledge/company.md` says nobody has answered them. Register the bot in
  **Settings → Bots** afterwards.

The pickers key a bot by its card's slug, so each template yields one bot there. A second bot from
the same template is one of the two command line routes; Tico gives it a slug of its own (`-2`).

## Adding a template

Tico ships 98 role templates, by group, each with a card. `templates/catalog/` also holds the four built-in bots and three helper bots, 105 cards in all. [Starter bots](starter-bots.md) lists the 98.

`templates/catalog/<template>/` is one template. The folder name is what `--template` takes.

- `card.yaml` describes the template to whoever is choosing. It is never copied into a bot's
  repository. Fields: `template`, `slug` (the default bot slug), `name`, `required`, `bootstrap`,
  `summary`, `owns`, `never`, `runtime`, `model`, `reasoning_effort`, `recommend_when`, `finance` (`rights` and one
  company `view`, given once at hire: `backend/template_finance.py`), `pack` (its group: `basics`, `sales`,
  `marketing`, `support`, `operations` or `engineering`), `lead` (on each group head), `group`, `icon`, `tags`, `suggest`,
  `team_templates`, `kind` (`helper` on a card that serves one human and sits outside the team chart), `pains`, `prerequisites` and, for a starter, `onboarding`, `first_routine`, `approval_required` and `example_output`. The
  server serves all of them except `onboarding` and `example_output`; the team builder reads `group`, `pack`, `lead`, `kind`, `icon`, `tags`,
  `suggest`, `pains`, `summary` and `recommend_when` ([The team builder](#the-team-builder)); `prerequisites` are shown by the bot's own setup, not by the wizard ([Starter bots](starter-bots.md)). After changing a card or `templates/groups.yaml`, run `python3 scripts/build_catalog_json.py` (the setup page's role list; Tico HQ builds its own copy from each published release, [docs/tico-hq.md](tico-hq.md#the-templates-hq-reads)) and `python3 scripts/build-icon-font.py` (a new icon). A card with a `first_routine` and an
  `onboarding` list is a **starter**: Create parks it (`needs_setup`), so its `onboarding` playbook must end with `hub bot setup-done`.
- Everything else in the folder is the repository the bot starts from: `AGENT.md`,
  `bot.yaml`, `playbooks/`, `knowledge/`, `memory/`, `state.md`, `.env.example`, `.gitignore`.
- `{{company_name}}`, `{{app_name}}`, `{{assistant_name}}` and `{{bot_name}}` are filled in every
  text file before the first commit, and in the card's own words wherever a human reads it.
- `required: true` means the wizard always includes it. `bootstrap: true` means the computer
  materializes it itself and no BotOps task is filed for it. Both are true for all four Built-in bots:
  Assistant, BotOps, Librarian and Strategy Bot.
- `recommend_when` says who a card is for. The team builder reads only `sells_to_businesses` and `sells_to_consumers` (a business-only card is suggested last to a team that sells only to consumers); the rest
  (`publishes_content`, `has_pipeline`, `uses_github`, ...) are descriptive and harmless. The `inbox` card (the message bot) needs a
  human's mailbox chosen whenever it is switched on.
- The release ships `templates/catalog` as `.yaml` and `.md` files only, which is all the server
  reads. The full folder is materialized from the checkout on the computer, so a template only works
  for real once that computer has pulled it. `TICO_CATALOG_DIR` points either side at another template folder.

## Troubleshooting

### Recover from interrupted strategy research

After a server restart, or when research runs past 10 minutes, **Your bets** offers the fields for you to fill in yourself.
A late answer from that stopped research does not change your draft.

To retry the research:

1. Reload the setup page to return to **What are you building**.
2. Check **Website** and **What you're building**, for example `acme.example` and "Cleaning for property managers."
3. Click **Next**.

Tico reuses matching research that has not failed, or starts again if it failed.
If the previous start received no confirmation, **Back** and **Next** also retry it.
Tico checks for that unconfirmed start before starting another run.
If research fails again, write your bets in the form and continue once the step's requirements are met.

To stop research you no longer want, go **Back**, clear both fields, and click **Next**.
The running research stops and **Your bets** shows the form. This keeps any strategy draft you have already saved.

### Other setup problems

**The wizard does not appear.** It is shown when `GET /api/v2/config` says `onboarding_needed`,
which is true only for the owner and only while setup has no completion time. Someone who is
not the owner is sent to Tasks, and a bot administrator can read the record but not write it.
Everyone else gets `403` on `GET /api/v2/setup`. To see the finished record again, open
`#/welcome` directly; it opens on the progress screen.

**Bots stay "Missing bot repository or AGENT.md".** For the assistant or BotOps it means no computer
is enrolled, or the bot sits on a computer that is not running, because a computer only materializes
its own assignments. Check **Settings → Bots** for the computer column and **Settings → Computers**
for the last heartbeat. A starter bot needs the same: the computer it is placed on sets it up, and until that computer
is online its row says *setting up*. For any other bot it means BotOps has not built it yet:
check that BotOps is `active`, that its repository exists, and read its setup task. The wizard's
rows say `waiting` until the computer reports a repository, and a bot no computer has reported on at
all is also `waiting`.

A bot placed on, or moved to, a computer that has never held it gets its repository by cloning it from GitHub
with its own token (the assignment names it). If that cannot happen the row says why instead of the generic
line: *not on GitHub yet* (create it with `hub bot repo-create <slug> --empty`; the computer that holds the
only copy publishes it while the bot is still assigned there), *GitHub refused the token*, or the clone's own
error. `hub health check` and Health list these as bots that cannot run. A move is refused up front when the
computer being left reports a repository GitHub does not have and the destination holds no copy.

**`422` on a template name.** `PUT /api/v2/setup` and `POST /api/v2/bots` refuse a template
Tico does not have, with the name in the detail. Check the folder exists under
`templates/catalog/` on the server, that it has a readable `card.yaml` with a `template:` field,
and that the key is a slug: lowercase letters, digits and single hyphens.

**No cards at all.** A missing or unreadable template folder leaves the wizard running with nothing to
offer, and the bots screen says so. On a hosted server that means the release did not carry
`templates/catalog`, or `TICO_CATALOG_DIR` points somewhere empty.

**A starter does not answer, or a routine never runs.** While it is `needs_setup` it answers only a human's chat message: a task
message, a Slack route, a bot's request and its routines wait. Press **Set up**, or say anything to it in its chat, and answer its
questions. When its setup is done it calls `hub bot setup-done`. If it cannot (a member's bot at their limit answers
`bot_limit`), archive a bot you no longer need or ask an admin to raise the limit in Settings > People. An owner or a bot's manager can
also call `POST /api/v2/bots/{bot}/onboarded` to release a bot whose conversation went wrong.

In **AI providers**, choose **Your company's plans or API keys** to show the provider checkboxes.
With none ticked, **Skip for now** continues without a provider; bots wait until one is added.
Settings > Models can also save with no providers enabled. After choosing providers, sign in on each computer
from that page, or store the model key in **Tools > Credentials** and grant **Every computer**. For a model API key or token
named `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `CLAUDE_CODE_OAUTH_TOKEN` or `CURSOR_API_KEY`, a blank **Bot variable name** is
inferred automatically; otherwise set the model's variable explicitly. Bot access still needs its own grant.
