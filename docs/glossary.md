# Glossary

The words Tico uses, and what each replaces.

## Who is on the team

- **Tico**: the product.
- **Team**: everyone, humans and bots. The sidebar section is **Team** and the chart is the team chart.
- **Group**: part of the team. A group can hold groups.
- **Teammate**: a human or a bot on the team.
- **Human**: a human teammate. The **Humans** page lists them.
- **Bot**: an AI teammate.
- **Task Manager**: a built-in helper bot that tidies tasks once an hour on weekdays: it closes what is plainly done, fixes obvious status and step drift, and leaves one Needs-you line per department for anything it is unsure of. It sits outside the org chart and never touches bots, credentials, sends or spending ([Task Manager](task-manager.md)).
- **Built-in**: a bot that comes with Tico, serves everyone and belongs to no group: Assistant, BotOps, Librarian, CEO Bot. The Task Manager and the Health Monitor are built-in helpers too, outside the chart. These four appear as **Built-in** in Settings > Bots. Assistant and BotOps have main rail entries; the CEO Bot is on Goals and Strategy, and the Librarian is on Docs and Strategy's Market tab. The Assistant, BotOps and the Librarian stay out of the team chart and goal owner list; the CEO Bot is at the top of the chart.
- **CEO Bot**: the built-in bot on every install that proposes the strategy to the owner, proposes the org chart that follows from it, and keeps KPIs and goal colours current. It sits under the owner at the top of the team chart, with the Heads under it ([Goals](goals-and-kpis.md#the-ceo-bot)). How much it does is a level only the owner picks, **Passive** by default ([Your CEO Bot](ceo-bot.md)). It never publishes a strategy version.
- **Strategy**: the section (`#/strategy`) where the team writes down its bets: a **summary** every bot reads on every run, a **vision**, three to five **bets**, and four sections (Who we serve, What we sell, What we know that others don't, What we won't do). The owner writes and publishes it; the CEO Bot proposes; **Market** is a tab under it ([Strategy](strategy.md)).
- **Bet**: one of the short decisions on the Strategy page that could turn out wrong, with a why, what it rules out, a **wrong-if**, and a status (proposed, active, retired). At most seven are active. Goals link to the bet they serve, and a bet's health comes only from its goals that have a KPI.
- **Wrong-if**: the observation that would retire a bet: a KPI crossing a threshold, a change to a market entity it cites, or a sentence with a check-by date ("not yet measurable"). Every active bet has one; when it trips, the CEO Bot puts a card in front of the owner.
- **Guide**: the interview questions the CEO Bot works from when it proposes a strategy, and the word for bets. Only the Default guide, in the style of YC and a16z, is offered. A guide never changes what the page stores. The other frameworks are lenses now.
- **Lens**: a short set of questions from a known framework (Blue Ocean, Scenarios, Pre-mortem, Where to Play...), answered once and run against the published strategy or the market in one model call. The result is read, never published; its changes are added to the draft one at a time by the owner ([Lenses](lenses.md)).
- **Version**: a published strategy, numbered, with a note. There is one draft at a time, the owner's; publishing stamps it and opens the next draft. Also a Tico release.
- **Company section**: the old Vision and Description at the top of Goals; both now live on the Strategy page, and Goals shows one line linking there ([Goals](goals-and-kpis.md#the-company-section)).
- **Why**: one line on a goal tying it to the team's vision. The CEO Bot proposes it and a person accepts it; a goal tied to a bet needs none ([Goals](goals-and-kpis.md#a-goals-why)).
- **Message bot**: a bot that works a human's email or a Slack channel, such as Inbox Manager. These appear separately under **Message bots** in the sidebar, Goals and setup.
- **External agent**: an agent outside the team connected to Tico (Codex, Claude, Grok), run by a human or a bot.
- **MCP** and **CLI**: two ways for humans and agents to reach the same shared Tico tools; an external agent connects over MCP with a token.
- **Owner**: the humans responsible for a bot, a goal or a computer.

## What a bot is made of

- **Instructions**: what a bot is told it does (`AGENT.md` in developer docs).
- **Tools**: everything a bot uses: its model and harness, GitHub, Slack, Google, AWS.
- **Model** and **Harness**: kinds of tool. The model (GPT 6.1 Sol, Claude Opus 5.5) and the harness it runs in (Codex, Claude Code).
- **AI provider**: the account a model comes from.
- **Profile**: what wakes a bot and which tasks it takes: Doer, Reviewer, Lead, Responder, Monitor or Custom ([Profiles](how-it-works.md#profiles)). It grants no right. Set it in Settings > Bots > Edit.
- **Credential**: a stored secret a bot is granted. "API key" or "token" only names what a service gives you, or sign-in.
- **Template**: what you create a bot from.
- **Setup**: a new bot's first conversation with its owner. Status **Needs setup**, button **Set up**. **Finish setup** is installing Tico.

## Where bots run

- **Computer**: what you register and add (Settings > Computers).
- **Connection**: a login an AI tool on a computer runs on: the **Computer's login** (a plan login or an API key), one per tool per computer, used by all its bots. See [Computers](computers.md).
- **Runner**: the software installed on a computer (install docs only).
- **Health**: what is wrong. Each problem is an **issue**.

## Work

- **Task**: work with a requester and an owner.
- **Routine**: a task that repeats on a schedule.
- **Needs you**: only when it needs the viewer. Otherwise **Needs <Name>**, as in "Needs Sam".
- **Approval**: a yes or no on one exact action.
- **Proposal**: a suggested change to a goal, a KPI or the strategy draft, for its owner to confirm. A `strategy_draft` proposal is how the CEO Bot puts a draft or a bet change in front of the owner.
- **Run**: one time a bot works.
- **Update**: a bot's daily or weekly post, and a new Tico version.
- **Message**, **Comment**, **Note**: a message is chat. A comment is on a task. A note is quiet, for a bot's next run ([leave work for the next run](using-tico.md#leave-work-for-the-next-run)).
- **Decision**: the typed-question model that classifies and decides.
- **Handoff**: delegating work by opening a child task and setting the parent to waiting.
- **Session**: one model conversation a bot resumes (a Claude Code or Codex thread). A bot has several ([Sessions](sessions.md)).
- **Main chat**: a bot's chat on its page, and its session. It sees task work only as one-line reports.
- **Task session**: the session for one task a bot owns, with its own room (thread) and worktree. Subtasks continue it by default.
- **Checkpoint**: a task's last turn reply, which a fresh session on the task starts from.
- **state.md**: a bot's own note of what it is doing and what to pick up next.

## Knowledge

Docs (internal and linked), Files, Meetings, Strategy (with Market as its tab), Listening, Goals, KPIs, Check-ins, Usage.

## Instead of

| Instead of | Use |
|---|---|
| company | team |
| employee | bot |
| employees | bots |
| coworker | teammate |
| coworkers | teammates |
| people page | Humans |
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
- **Copy**: an independent bot in a separate repository, created by `hub bot copy`.
