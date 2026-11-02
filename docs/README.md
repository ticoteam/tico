# Tico documentation

[Start here](install.md) · [Use Tico](using-tico.md) · [Navigation](navigation.md) · [Glossary](glossary.md)

Tico coordinates a Team of humans and bots. A small server stores the app and team data; computers run bots with the team's
model subscriptions. The server runs no bots or model CLIs; it may call your configured Decision provider. Setup suggestions
from Tico HQ are on by default and share your answer and team description; [Privacy](../PRIVACY.md) lists the payload and controls.

## New owner: get one bot working

Try locally with Docker on a Mac with Docker Desktop running, or on Linux:

```bash
curl -fsSL https://github.com/ticoteam/tico/releases/latest/download/install.sh -o install.sh && sh install.sh --local --owner-email you@example.com
```

[Start here](install.md#quick-start-on-your-own-computer) continues through provider sign-in to
[Your first result](install.md#your-first-result): Content Marketer drafts a post from a fictional brief, you review it and close the task.
Use `releases/download/vX.Y.Z/install.sh` to pin a release. For a shared server, follow [Hosted install](install.md#install-the-server-for-your-team).

- [Build your team](onboarding-guide.md), [Team chart](org-chart.md) and [People](people.md).
- [The sample company](sample-company.md): a made-up five-bot team to try Tico with.
- [Choose and set up bots](creating-bots.md), with the full [Starter catalog](starter-bots.md).
- [Connect tools](connect-tools.md) (including [a specialist outside Tico](connect-tools.md#work-with-a-specialist-outside-tico) and [hiring an external AI specialist](connect-tools.md#hire-an-external-ai-specialist)), [Credentials](credential-vault.md) and [Permissions](permissions.md).
  [Tico AI Gateway](ai-gateway.md) runs bots on Tico picks (a department, a level, Fast or not) with one key from Tico.
  Members can have 25 active bots by default; the owner can change the limit.
- Hosted Tico: [Joining the Tico waitlist](waitlist.md), [Your Tico account page](account.md), and
  [Ask for more credit](ask-for-credit.md). Account-page help is **unreleased** while deployment is verified.
- Keep Tico running: [Health and recovery](install.md#operating-it), [Updates](updates.md),
  [Backups and restore](install.md#backups-and-restore) and [Support](support.md).

## Team member: use it every day

- [Use Tico](using-tico.md): your team's address, tasks, chats, results and Needs you.
- [Sessions](sessions.md): each task a bot owns is its own session, apart from its main chat.
- [Desktop app](desktop.md), [Your Assistant](assistant.md) and [External agents](connect-an-agent.md).
- Knowledge and results: [Docs](docs.md), [Librarian](librarian.md), [Market watch](market-watch.md), [Weekly briefing](market-briefing.md), [Files](files.md), [Pages](pages.md), [Meetings](meetings.md), [Listening](listening.md), [Physical mail](physical-mail.md).
- [Task Manager](task-manager.md): the built-in helper that tidies tasks once an hour on weekdays.
- [Bookkeeper](bookkeeper.md): the built-in helper that keeps the books, and the [Finance tools](finance-tools.md) it and every bot use.
- [Strategy](strategy.md), [Growth](growth.md) (ideas, tests, channel economics, Brand and GTM), [Lenses](lenses.md), [Goals and KPIs](goals-and-kpis.md), [Your Strategy Bot](strategy-bot.md), [People](people.md), [Groups](org-chart.md) and [Access](permissions.md).
- [Glossary](glossary.md) and [Help](support.md).

## Developer or computer operator

- [Register a computer](install.md#add-computers-to-run-your-bots), its logins and usage on [Computers](computers.md), [Harnesses](harnesses.md) and [Environments](environments.md).
- [Build a bot](creating-bots.md), [Visuals in replies](visuals.md), [Routines](routines.md), [Webhooks](webhooks.md) (recipes for [GitHub](webhooks-github.md), [Stripe](webhooks-stripe.md), [Zapier, Make and n8n](webhooks-zapier-make-n8n.md)), [System routines](system-routines.md), [Agent context](agent-context.md), [Watchers](watchers.md),
  [Sessions](sessions.md) and [Task worktrees](worktrees.md).
- [API](api.md), [Custom frontend](custom-frontend.md), [Listening API](listening.md#save-decide-and-resolve),
  [Needs you batches](needs-you-batches.md) and [Service keys](service-keys.md), for another system that files tasks.
  [Recorder speech and assist](recorder.md) relays the Tico Recorder's transcription, live text and chat through the Gateway.
- Clients written against the old names: [Upgrading to the contract release](upgrading-to-contract-release.md).
- External agents: [Common setup](connect-an-agent.md), [Hermes](hermes-agents.md), [OpenClaw](openclaw-agents.md), [Grok Bot and Dots](external-agent-sync.md).
- Advanced operations: [Cloud provisioning](install-advanced.md), [Cloud machines](cloud-machines.md), [Many companies on one host](cell.md) and its [stats and debug tools](cell-stats.md), [Sizing](sizing.md), [Observability](observability.md),
  [Databases](databases.md), [Query Tico data](hub-sql.md), [Slack gateway](slack-gateway.md).
- Project maintenance: [Architecture](architecture.md), [Contributing](../CONTRIBUTING.md), [Releasing](releasing.md),
  [Runner wire contract](runner-wire-contract.md), [Tico HQ](tico-hq.md) and [Telemetry](telemetry.md).
- Staff only: [Manage a company's billing](staff-billing.md) (credit, minimum balance, grace, enforcement, reversals) and
  [Review the Hosting waitlist](staff-waitlist.md).

[Task types and steps](tasks.md): custom pipelines, status mapping, and CLI, MCP, API and SQL examples;
editing and deleting your own comments.
