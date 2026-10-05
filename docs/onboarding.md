# Finish setup

The first time the owner opens a new environment, the app is a wizard at `#/welcome`: name the
team, say what it does, build its team chart group by group, add a computer (a Linux Docker runner or a Mac), optionally connect your own
external agent, and press **Create my team**. Every team gets the assistant, BotOps, the Librarian and the Goal Manager, and none of them can be archived.
Creating defines the bots on the server and hands the rest to two places: a starter bot's repository is set up by the computer
the moment it is placed, and BotOps sets up every other template.

The wizard is the first of three pieces, all described below: the wizard, a short tour, and a line under the team list
that points at BotOps. [After the wizard](#after-the-wizard) covers the last two, and the Market page's empty state, where the
owner asks for market research. To choose a first team and get the most from it, read the [setup guide](onboarding-guide.md).

Nobody else sees the wizard. Only the owner may write it, and the sidebar entry
**Finish setup** appears only while it is unfinished.

The implementation is `backend/onboarding.py` (the record, creating the team), `backend/recruit.py` and `backend/recruit_rank.py`
(the team builder's templates and suggestions), `ui/org-builder.js` (the team chart screen), `ui/first-run.js` (who reports to whom, the review
and the screen after Create), `hq/recruit.py` (Tico HQ's suggestions), `clients/catalog.py` (turning a template into a repository),
`runner/service.py` (the bots the computer sets up itself) and `templates/catalog/` (the templates). The quick start in the
[README](../README.md) is the same flow with the commands in it.

## The screens

Every **Next** saves the whole draft with `PUT /api/v2/setup`, so a closed tab loses nothing.

| Screen | What it asks | What it stores |
|---|---|---|
| AI providers | Only while no provider is chosen yet; otherwise the wizard starts at Names. Which AI providers the bots may use. Optional: leave none ticked and Skip for now moves on | The team's providers, as Settings > AI providers saves them |
| Names | **Team/Company name**, app name, and optionally your own name | `names`. From the moment they are saved they override `TICO_TEAM_NAME` (the old `TICO_COMPANY_NAME` remains an alias) and `TICO_APP_NAME` everywhere, including in the template cards. **Your name** (`names.owner_name`) is saved on the owner's roster entry, so the team chart and the sidebar show it instead of the address; it is prefilled from the roster, or from the display name the sign-in proxy vouches for (a `name` claim from Cloudflare Access or the AWS load balancer), and left blank it changes nothing. **Team email domain** (`names.team_domain`) is asked only when the owner signs in with public email such as gmail.com and no team address is on the roster; it is optional, and lets members add teammates at that domain (it does not let anyone sign in). The wizard does not ask for an assistant name: the tab is always called Assistant, and `names.assistant_name` is `TICO_ASSISTANT_NAME`, which is `Assistant` unless set (a name equal to the team's reads as `Assistant`) |
| About the team | What you do, who you sell to, whether software is your product, and team size | `answers`. Whether software is the product decides which groups start picked, and the description helps the suggestions. It is also written into every bot's `knowledge/company.md` |
| Your team chart | The groups, then one question per group and the bots to recruit into it, with the chart growing beside it, and the message bot switch under the finished chart ([The team builder](#the-team-builder)) | `answers.departments`, `answers.briefings` and `selected`: for each chosen slug, its template, display name, the `AGENT.md` text and `reports_to` (a human `human:<id>` or a bot slug). Nothing is created yet |
| Add the computer that runs your bots | If a runner is already online (the server's own) the step is one line, `<label> online`. Otherwise the kind of computer, **A Linux or cloud server (Docker)** when the server itself runs in Docker (`in_docker` in `GET /api/v2/config`, the usual install) and **A Mac** when it does not, then a one-time code or setup file and the commands | Nothing. It polls `GET /api/v2/setup` every ten seconds and reports the enrolled computer |
| Connect an external agent | Optional: **Connect an external agent** makes a personal token and the MCP setup to paste into Grok, Dots, Muse or any MCP agent ([Connect an external agent](connect-an-agent.md)) | Nothing in the record; the token is the owner's own (`POST /api/v2/me/tokens`) |
| Review and create | A summary of all of it, the team with each bot's reports-to | **Create my team** calls `POST /api/v2/setup/complete` |
| After Create | One screen, below | Nothing in the record |

### The answers

| Field | Shape |
|---|---|
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
| `POST /api/v2/setup/recruit` | Body `{department, briefing, share}` (`briefing` at most 500 characters). Answers `{bots: [{template_id, why}], suggested_default: [template_id], source: "hq" \| "local", shared, off_by}` |

Both are for the owner and bot administrators, and neither is part of the stable v2 contract (like the rest of the wizard's routes).

The group card carries a toggle, **Suggestions from Tico HQ**, with a privacy link and the exact payload: group, answer,
team description, customer type, software choice, catalog version and install ID (when counting is on and its notice has been shown). It is on by default and off (and disabled, with the
reason) when the install may not ask HQ. The server asks Tico HQ (`POST <TICO_HQ_URL>/v1/recruit`, [Tico HQ](tico-hq.md)) only when the
toggle is on **and** none of these is true: demo mode, `TICO_TELEMETRY=off`, `DO_NOT_TRACK` set, or the anonymous usage count switched off
in Settings (`hq.off_by` says which). It sends the group, the answer, three facts from "About the team" (`what`, at most 500
characters; `sells_to`; `software`), the template version and, while the usage count is on and its notice has been shown, its install id; it waits at most 6 seconds and
keeps only template ids that are this group's in its own templates. HQ answers template ids and a short why, never text a bot would
follow. What is sent and kept is in [PRIVACY.md](../PRIVACY.md).

Otherwise, or on any failure, the **local recommender** (`backend/recruit_rank.py`) answers, with no network: the head first, then the
cards the answer matches (a tag, or two words of a longer tag phrase; a word of the name; two or more words of the summary; the answer
counts double "What you do", and the group's own name counts for nothing), then `default` and `common` cards; a `niche` card only when the answer names it; a business-only card last for a team that sells
only to consumers. The why is the words that matched ("Matches “resellers”"), else "Heads Sales and reports to you", "A starting point for
Sales" or "Common in Sales". The same answer always gives the same list.

### What Create does

**Create my team** (`POST /api/v2/setup/complete`) writes to the server only, in one request (a chart of 25 bots takes a fraction of a
second). It reaches no computer.

- BotOps, the assistant, the Librarian and the Goal Manager first, so they exist before anything is addressed to them, then everything picked, each `planned`
  with the card's summary as its description, `bot-<slug>` as its repository, the card's runtime, model and reasoning effort, the human
  it reports to, and the owner as its owner. A model a deployment does not offer falls back to the product default.
- **No AI provider and no computer are needed.** The team is created first. With no provider chosen a bot names no runtime or model and
  follows the team default, so it runs once a provider is added in Settings > AI providers. With no computer enrolled the bots stay
  `planned`; when the owner's computer enrolls, the bots are placed on it and activated. Until then the after-Create screen says
  "Waiting for a computer" or "Add an AI provider".
- Each bot goes in the **group** its template belongs to (Marketing, Product, Engineering ...); the group is made if the team has none yet,
  and one it already has by that name is reused ([the team chart](org-chart.md#groups)). Built-in bots stay outside groups.
- The template and the reviewed instructions are stored in the bot's server-side config, with the **template version** (the release whose
  templates it came from: `template_version`).
- A **starter** template (a card with a `first_routine` and an `onboarding` conversation) is created whole and parked:
  - `onboarding_state: needs_setup`, exposed on `/api/v2/bots`, `/api/v2/bots/{bot}` and `/api/v2/org`;
  - its first routine is seeded off (the template declares `enabled: false`) and goes on when its setup starts (**Start setup**, a first message from someone who manages it, or go-live), so nobody approves it separately;
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
- Finishing twice is safe: it reopens no task, creates no second bot, and keeps the first completion time.

### Needs setup

A parked starter shows **Needs setup** on the team chart (a small *Setup* mark), on its page and after Create. **Set up** sends it
the message "Let's set you up." as the human, in the human's own chat with the bot: the conversation the bot page's **Chat** tab shows,
open or not when the button is pressed. It starts the setup conversation its `AGENT.md` describes; any first message from a human
does the same. While the bot is parked, the runner gives its chat runs one Setup instruction in place of the generic chat rules: follow
the template's `onboarding` section and `playbooks/onboarding.md`, ask the questions and stop, and until the human has answered run no
tool that reaches another system, file no task and edit no file, never `AGENT.md`. The bot introduces itself, asks the template's questions in one message, writes a first draft from the team's own
data, and tells the human what its first routine does; starting the setup already switched that routine on. When its answers and first
result are recorded it calls `hub bot setup-done` (MCP `hub_bot_setup_done`, `POST /api/v2/bots/{bot}/onboarded`). That clears the mark,
lets its routines run and counts it toward a member's limit. Until then it stays parked and answers humans only.

### After Create: one screen

- **Finish setup**: each bot with its progress. A starter says *Setting up its repository* until the repository exists,
  then its **Set up** works.
- **AI providers**: the first link opens Settings > AI providers so bots can start working.
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
wizard  ->  tour (once)  ->  "Talk to BotOps" line
             replay: ?        until a bot of your own exists
```

### The tour

Six spotlight steps: Updates, Tasks, your bots (the team list), Docs, Market, Meetings. **Next** moves
on, **Skip** or **Esc** closes it, and focus stays inside it. On a phone it opens the navigation drawer
and shows the same steps. It opens by itself once, right after **Finish setup**, and can be replayed
from **?** (How Tico works) with **Take the tour**. That it was seen is kept per human.

### Bots

There is no checklist, no card above any page and no card in the sidebar. While there are no bots of your own (the Create your
first bot step is not done), one line of muted text sits under the team list: "Talk to BotOps to add or edit your bots",
with **BotOps** linking to its chat (`#/bot/botops`). It shows only to humans who may add bots (the owner and bot administrators,
`can_build`), and has nothing to close. Asking BotOps in chat is how a bot is added (`playbooks/build-me-a-bot.md`); connecting
an external agent you already have is the **Connect an external agent** button in the sidebar footer.

### An empty Market page

While the market has no entity and no page someone wrote (the seed's pages do not count), the Market page is an empty state
instead of the graph, the index and the ask box. The owner sees one box for a website, a description
or links to anything about the market, **Start research**, and an **Attach files** link to the Docs import. Everyone else sees
"Nothing here yet."

**Start research** calls `POST /api/v2/setup/getting-started/market` `{"text"}`, which files one task, "Set up the market map", to the
Librarian and answers `{"task_id", "bot": "librarian"}`; `409 librarian` while the Librarian is not running. The Librarian's
`playbooks/market-setup.md` researches the sources and writes the market pages and graph ([librarian.md](librarian.md)). The page
then shows "The Librarian is researching your market. This usually takes 5–10 minutes." with a link to the task, until the market
has content (at least two minutes, at most thirty), kept in the browser, polling the market every 30 seconds. Then the normal
Market page is drawn. The code is `ui/market-page.js`.

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
read the task, `hub bot create <slug> --template <template> --name "<Display>"`, put the reviewed
instructions into `AGENT.md` (or tailor the template's to the answers), run `hub bot check <slug>`
and fix every failure, then commit the result.

For a human's authorized chat request, BotOps finishes the work: it places the bot on a computer,
turns it on, starts Setup, opens [Credential cards](credential-vault.md#credentials-asked-for-in-the-chat)
for missing values, runs one small test and reports the result in chat. It stores and grants
Credentials with the requester's rights; values stay out of messages.

An unattended setup task prepares and checks the repository, then reports readiness, the repository
path and any missing Tool or Credential to the owner. It leaves activation to the owner in that case.
An existing repository is checked and updated for the requested work, rather than overwritten.

## The assistant, BotOps, the Librarian and the Goal Manager are built in

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

The fourth, the [Goal Manager](goals-and-kpis.md#the-goal-manager), keeps the KPIs and sets goals' automatic colours. It is
built the same way (a required, bootstrap card; a team from before it existed gets it on update once a model is chosen
and a computer is enrolled). Its routines start paused: the server arms the daily KPI pass once the first KPI exists, and the
weekly goals review stays paused until the Goal Manager arms it after the owner has read the first one.

A team that set the assistant aside before it was built in (v0.2.1 to v0.2.9 let the wizard skip it) keeps it archived
on update: nothing restores it automatically. Its owner sees "The Assistant is off" on the Assistant page and at the top of
Settings > Bots, and one click on **Turn on Assistant** brings the same bot back with its history (or adds it from its
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

- **Settings → Bots → Add from template.** The same cards, minus the bots that already exist. A starter is created parked, exactly as Create
  makes it; any other template is created `planned` with the same BotOps task the wizard would have filed.
- **From a BotOps task**, inside a run: `hub bot create <slug> --template <template> --name
  "<Display>"`, then `hub bot check <slug>`. Both need `HUB_WORKSPACE`, which the runner puts in
  every run's environment.
- **By hand on the Mac**: `scripts/tico -e <env> bot create <slug> --template <template>`. This
  materializes from the same templates with the environment's names but without the wizard
  answers, so `knowledge/company.md` says nobody has answered them. Register the bot in
  **Settings → Bots** afterwards.

The pickers key a bot by its card's slug, so each template yields one bot there. A second bot from
the same template is one of the two command line routes, with a slug of your own.

## Adding a template

Tico ships 93 templates, by group, each with a card; [Starter bots](starter-bots.md) lists them all.

`templates/catalog/<template>/` is one template. The folder name is what `--template` takes.

- `card.yaml` describes the template to whoever is choosing. It is never copied into a bot's
  repository. Fields: `template`, `slug` (the default bot slug), `name`, `required`, `bootstrap`,
  `summary`, `owns`, `never`, `runtime`, `model`, `reasoning_effort`, `recommend_when`, `pack` (its group: `basics`, `sales`,
  `marketing`, `support`, `operations` or `engineering`), `lead` (on each group head), `group`, `icon`, `tags`, `suggest`,
  `team_templates`, `kind` (`helper` on a card that serves one human and sits outside the team chart), `pains`, `prerequisites` and, for a starter, `onboarding`, `first_routine`, `approval_required` and `example_output`. The
  server serves all of them except `onboarding` and `example_output`; the team builder reads `group`, `pack`, `lead`, `kind`, `icon`, `tags`,
  `suggest`, `pains`, `summary` and `recommend_when` ([The team builder](#the-team-builder)); `prerequisites` are shown by the bot's own setup, not by the wizard ([Starter bots](starter-bots.md)). After changing a card or `templates/groups.yaml`, run `python3 scripts/build_catalog_json.py` (Tico HQ's copy) and `python3 scripts/build-icon-font.py` (a new icon). A card with a `first_routine` and an
  `onboarding` list is a **starter**: Create parks it (`needs_setup`), so its `onboarding` playbook must end with `hub bot setup-done`.
- Everything else in the folder is the repository the bot starts from: `AGENT.md`,
  `bot.yaml`, `playbooks/`, `knowledge/`, `memory/`, `state.md`, `.env.example`, `.gitignore`.
- `{{company_name}}`, `{{app_name}}`, `{{assistant_name}}` and `{{bot_name}}` are filled in every
  text file before the first commit, and in the card's own words wherever a human reads it.
- `required: true` means the wizard always includes it. `bootstrap: true` means the computer
  materializes it itself and no BotOps task is filed for it. Both are true for all four Built-in bots:
  Assistant, BotOps, Librarian and Goal Manager.
- `recommend_when` says who a card is for. The team builder reads only `sells_to_businesses` and `sells_to_consumers` (a business-only card is suggested last to a team that sells only to consumers); the rest
  (`publishes_content`, `has_pipeline`, `uses_github`, ...) are descriptive and harmless. The `inbox` card (the message bot) needs a
  human's mailbox chosen whenever it is switched on.
- The release ships `templates/catalog` as `.yaml` and `.md` files only, which is all the server
  reads. The full folder is materialized from the checkout on the computer, so a template only works
  for real once that computer has pulled it. `TICO_CATALOG_DIR` points either side at another template folder.

## Troubleshooting

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
`bot_limit`), archive a bot you no longer need or ask an admin to raise the limit in Settings > Humans. An owner or a bot's manager can
also call `POST /api/v2/bots/{bot}/onboarded` to release a bot whose conversation went wrong.

In **AI providers**, **Skip for now** continues without a provider; bots wait until one is added.
Settings > AI providers can also save with no providers enabled. After choosing providers, sign in on each computer
from that page, or store the model key in **Tools > Credentials** and grant **Every computer**. For a model API key or token
named `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `CLAUDE_CODE_OAUTH_TOKEN` or `CURSOR_API_KEY`, a blank **Bot variable name** is
inferred automatically; otherwise set the model's variable explicitly. Bot access still needs its own grant.
