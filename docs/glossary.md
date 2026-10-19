# Glossary

The words Tico uses, and what each replaces.

## Who is on the team

- **Tico**: the product.
- **Team**: everyone, humans and bots. The sidebar section is **Team** and the chart is the team chart.
- **Group**: part of the team. A group can hold groups.
- **Teammate**: a human or a bot on the team.
- **Human**: a human teammate. The **Humans** page lists them.
- **Bot**: an AI teammate.
- **Built-in**: a bot that comes with Tico, serves everyone and belongs to no group: Assistant, BotOps, Librarian, Goal Manager. These four appear as **Built-in** in Settings > Bots. Assistant and BotOps have main rail entries; the Goal Manager is on Goals and the Librarian is on Docs and Market. They stay out of the team chart and goal owner list.
- **CEO Bot**: the Goal Manager's name on an install with the CEO Bot switch on. It then sits under the owner on the team chart, with the Heads under it ([Goals](goals-and-kpis.md#the-ceo-bot-switch)).
- **Company section**: the Vision and Description at the top of Goals, shown only with the CEO Bot switch on ([Goals](goals-and-kpis.md#the-company-section)).
- **Why**: one line on a goal tying it to the company's Vision. The CEO Bot proposes it and a person accepts it ([Goals](goals-and-kpis.md#a-goals-why)).
- **Message bot**: a bot that works a human's email or a Slack channel, such as Inbox Manager. These appear separately under **Message bots** in the sidebar, Goals and setup.
- **External agent**: an agent outside the team connected to Tico (Codex, Claude, Grok), run by a human or a bot.
- **MCP** and **CLI**: two ways for humans and agents to reach the same shared Tico tools; an external agent connects over MCP with a token.
- **Owner**: the humans responsible for a bot, a goal or a computer.

## What a bot is made of

- **Instructions**: what a bot is told it does (`AGENT.md` in developer docs).
- **Tools**: everything a bot uses: its model and harness, GitHub, Slack, Google, AWS.
- **Model** and **Harness**: kinds of tool. The model (GPT 6.1 Sol, Claude Opus 5.5) and the harness it runs in (Codex, Claude Code).
- **AI provider**: the account a model comes from.
- **Credential**: a stored secret a bot is granted. "API key" or "token" only names what a service gives you, or sign-in.
- **Template**: what you create a bot from.
- **Setup**: a new bot's first conversation with its owner. Status **Needs setup**, button **Set up**. **Finish setup** is installing Tico.

## Where bots run

- **Computer**: what you register and add (Settings > Computers).
- **Connection**: a login an AI tool on a computer runs on: a **subscription** (a named plan login), the **Computer's login**, or an API key. See [Computers](computers.md).
- **Runner**: the software installed on a computer (install docs only).
- **Health**: what is wrong. Each problem is an **issue**.

## Work

- **Task**: work with a requester and an owner.
- **Routine**: a task that repeats on a schedule.
- **Needs you**: only when it needs the viewer. Otherwise **Needs <Name>**, as in "Needs Sam".
- **Approval**: a yes or no on one exact action.
- **Proposal**: a suggested change to a goal or KPI, for its owner to confirm.
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

Docs (internal and linked), Files, Meetings, Market, Listening, Goals, KPIs, Check-ins, Usage.

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
