# Glossary

The words Tico uses, and what each replaces.

## Who is on the team

- **Tico**: the product.
- **Team**: everyone, humans and bots. The sidebar section is **Team** and the chart is the team chart.
- **Group**: part of the team. A group can hold groups.
- **Teammate**: a human or a bot on the team.
- **Human**: a human teammate. The **People** tab in Settings lists them.
- **Bot**: an AI teammate.
- **General Manager**: a built-in helper bot that manages the General pipeline and tidies tasks once an hour on weekdays:
  tasks that say Paused, held bots and duplicates. It sits outside the org chart and never touches bots, credentials,
  sends or spending ([General Manager](general-manager.md)).
- **Built-in**: a bot that comes with Tico and serves everyone: Assistant, BotOps, Librarian, Strategy Owner, General Manager, Finance Owner, Rules Owner, Design Owner, Updates Owner, Automations Owner. The General Manager reports to the owner and runs the General pipeline; BotOps, the Librarian, the Strategy Owner and the Rules, Design, Updates and Automations Owners are its team (the General group). The Assistant and the Finance Owner report to the owner. They all appear as **Built-in** in Settings > Bots. Ask (bottom left, ⌘J; the phone's middle tab) opens the Assistant, which hands BotOps what needs it; the Strategy Owner is on Goals and Strategy, and the Librarian is on Docs and Strategy's Market tab. The Assistant, BotOps and the Librarian stay out of the team chart and goal owner list; the Strategy Owner stays out of the team chart too.
- **Strategy Owner**: the built-in bot on every install that owns the **Strategy** section, the way the Librarian owns Docs and Market. It proposes the strategy to the owner, proposes the org chart that follows from it, and keeps KPIs and goal colours current. It sits under the owner at the top of the team chart, with the Heads under it ([Goals](goals-and-kpis.md#the-strategy-bot)). By default it suggests actions and acts only when a person says yes. The owner picks its level: **Passive**, **Suggests actions** (the default), or **Acts on its own**, which lets it act without asking ([Your Strategy Owner](strategy-bot.md)). It never publishes a strategy version.
- **Strategy**: the section (`#/strategy`) where the team writes down its bets: a **summary** every bot reads on every run, a **vision**, three to five **bets**, and seven sections (Who we serve, What we sell, How we know they want it, Why now, What we know that others don't, How we sell, What we won't do). The owner writes and publishes it; the Strategy Owner proposes; **Market** is a tab under it ([Strategy](strategy.md)).
- **Bet**: one of the short decisions on the Strategy page that could turn out wrong, with a why, what it rules out, a **wrong-if**, and a status (proposed, active, retired). At most seven are active. Goals link to the bet they serve, and a bet's health comes only from its goals that have a KPI.
- **Wrong-if**: the observation that would retire a bet: a KPI crossing a threshold, a change to a market entity it
  cites, or a sentence with a check-by date ("not yet measurable"). Every active bet has one; when it trips, the
  Strategy Owner puts it in front of the owner.
- **Guide**: the interview questions the Strategy Owner works from when it proposes a strategy, and the word for bets. Only the Default guide, in the style of YC and a16z, is offered. A guide never changes what the page stores. The other frameworks are lenses now.
- **Lens**: a short set of questions from a known framework (Blue Ocean, Scenarios, Pre-mortem, Where to Play...), answered once and run against the published strategy or the market in one model call. The result is read, never published; its changes are added to the draft one at a time by the owner ([Lenses](lenses.md)).
- **Version**: a published strategy, numbered, with a note. There is one draft at a time, the owner's; publishing stamps it and opens the next draft. Also a Tico release.
- **Company section**: the old Vision and Description at the top of Goals; both now live on the Strategy page, and Goals shows one line linking there ([Goals](goals-and-kpis.md#the-company-section)).
- **Why**: one line on a goal tying it to the team's vision. The Strategy Owner proposes it and a person accepts it; a goal tied to a bet needs none ([Goals](goals-and-kpis.md#a-goals-why)).
- **Message bot**: a bot that works a human's email or a Slack channel, such as Inbox Manager. These appear separately under **Message bots** in the sidebar, Goals and setup.
- **External agent**: an agent outside the team connected to Tico (Codex, Claude, Grok), run by a human or a bot.
- **MCP** and **CLI**: two ways for humans and agents to reach the same shared Tico tools; an external agent connects over MCP with a token.
- **Owner**: the humans responsible for a bot, a goal or a computer.

## What a bot is made of

- **Instructions**: what a bot is told it does (`AGENTS.md` in developer docs).
- **Tools**: everything a bot uses: its model and harness, GitHub, Slack, Google, AWS.
- **Model** and **Harness**: kinds of tool. The model (GPT 6.1 Sol, Claude Opus 5.5) and the harness it runs in (Codex, Claude Code).
- **AI provider**: the account a model comes from.
- **Profile**: what wakes a bot and which tasks it takes: Doer, Reviewer, Lead, Manager, Responder, Monitor or Custom ([Profiles](how-it-works.md#profiles)). It grants no right. Set it in Settings > Bots > Edit.
- **Credential**: a stored secret a bot is granted. "API key" or "token" only names what a service gives you, or sign-in.
- **Template**: what you create a bot from.
- **Setup**: a new bot's first conversation with its owner. Status **Needs setup**, button **Set up**. **Finish setup** is installing Tico.

## Where bots run

- **Computer**: what you register and add (Settings > Computers).
- **Connection**: a login an AI tool on a computer runs on: the **Computer's login** (a plan login or an API key), one per tool per computer, used by all its bots. See [Computers](computers.md).
- **Runner**: the software installed on a computer (install docs only).
- **Health**: what is wrong. Each problem is an **issue**.

## Work

- **Task**: work with a requester and an owner. It is `open`, `done` or `closed` ([Tasks](tasks.md)).
- **Pipeline**: a line of stages a task moves through, with a manager bot. Every task is in one; General is the default
  ([Tasks](tasks.md#pipelines-and-stages)).
- **Stage**: one place in a pipeline, with what a task needs to leave it.
- **Ask**: a requirement on a task that names a person: an OK or an answer. It is in their Needs you
  ([Tasks](tasks.md#asks-and-oks)).
- **OK**: a person's yes on an ask. An OK for something that leaves the team or spends is bound to exactly what they
  were
  shown.
- **Snooze**: the one way to set a task aside, until a date or with no end.
- **Routine**: a task that repeats on a schedule.
- **Needs you**: only when it needs the viewer. Otherwise **Needs <Name>**, as in "Needs Sam".
- **Proposal**: a suggested change to a goal, a KPI or the strategy draft, for its owner to confirm. A `strategy_draft` proposal is how the Strategy Owner puts a draft or a bet change in front of the owner.
- **Run**: one time a bot works.
- **Update**: a bot's daily or weekly post, and a new Tico version.
- **Message**, **Comment**: a message is said in a thread. A comment is on a task.
- **Thread**: a conversation with an audience and members, listed under Threads ([Threads](threads.md)).
- **DM**: a private thread for exactly the people in it. Starting one with the same people reuses it.
- **Sub-thread**: a side topic under one message, with its own read state.
- **Call**: a Tico Meet call started in a thread ([Meetings](meetings.md)).
- **Steer**, **Queue**, **Defer**: how a message, comment or new task reaches a bot. Steer: into its running turn now. Queue: right after that turn, or now. Defer: with its next turn there, at the latest after a deadline ([Steer, queue or defer](using-tico.md#steer-queue-or-defer)).
- **Deferred message**, **deferred task**: deferred to a bot's next run of any kind; once called a next-run task ([leave work for the next run](using-tico.md#leave-work-for-the-next-run)).
- **Decision**: the typed-question model that classifies and decides.
- **Handoff**: delegating work by filing a sub-task; the parent waits on the tasks it filed.
- **Session**: one model conversation a bot resumes (a Claude Code or Codex thread). A bot has several ([Sessions](sessions.md)).
- **Home**: a bot's or a person's public thread, where a bot's open asks wait. A bot's Home is its main session's thread; it sees task work only as one-line reports. A person's personal chat with a bot moves into that bot's Home with its messages, unless its owner may not read that Home; the Assistant's and the Strategy Owner's chats, the Librarian's per-person docs chats, and chats with a bot synced from another app, stay where they are. Everyone who can read the Home can read those messages, except one whose own references name a task, a file under `target`, `answer`, `review` or `body_sources`, or a meeting under `live_meeting` or `live_meetings`, that its reader may not read, or one that replies to, answers, or takes its inputs from a message naming a task, or a meeting under `live_meeting` or `live_meetings`, that its reader may not read, directly or through a chain of such links. The check does not read the words typed into a message or the files attached to it ([Threads and their audiences](permissions.md#threads-and-their-audiences)).
- **Task session**: the session for one task a bot owns, with its own thread and worktree. Subtasks continue it by default.
- **Checkpoint**: a task's last turn reply, which a fresh session on the task starts from.

## Knowledge

Docs (internal and linked), Files, Meetings, Strategy (with Market as its tab), Goals, KPIs, Check-ins, Usage.

- **Listening**: an automation of one special kind, under Automations in the sidebar. Its sources say what to read, its rules keep and tag or drop, and its rate limits say how many items are sent as text to its steps ([Listening](listening.md)).

## Webhooks

- **Incoming webhook** (also **trigger** in the CLI and API): an address outside senders post to. Tico keeps each call, starts the routines and rules that listen, and can wake a bot. Automations → Incoming webhooks ([Webhooks](webhooks.md#incoming)).
- **Outgoing webhook** (also **endpoint**): an address Tico posts events to when something happens in Tico, signed as Standard Webhooks. Tools → Add tool → Outgoing webhook ([Webhooks](webhooks.md#outgoing)).
- **Relay address**: a second address for an incoming webhook, held at Tico Gateway, that keeps what is sent while the server is off or asleep. Settings → Models → Tico Gateway → Webhooks ([Webhooks](webhooks.md#through-tico-gateway)).
- **Sleep when idle**: on a server Tico hosts, stop the server after quiet hours and start it again on a Gateway webhook, a routine, or a sign-in ([Account](account.md#sleep-when-idle)).

## Instead of

| Instead of | Use |
|---|---|
| company | team |
| employee | bot |
| employees | bots |
| coworker | teammate |
| coworkers | teammates |
| Humans page | People |
| onboarding | setup |
| standing instructions | Instructions |
| machine | Computer |
| machines | Computers |
| integration | Tools |
| integrations | Tools |
| API keys | Credentials |
| judge | Decision |
| hub | Tico |

## Branches

- **Branch**: a person's bot that follows an original's instructions, model settings and repository, with personal tasks and chats.
- **Allow branches**: the original's setting, off by default.
- **Make my branch**: create or open your branch on your own computer.
- **Copy**: an independent bot in a separate repository, created by `tico bot copy`.
