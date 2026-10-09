# Tico documentation

[Start here](install.md) · [Use Tico](using-tico.md) · [Navigation](navigation.md) · [Glossary](glossary.md)

Tico coordinates a Team of humans and bots. A small server stores the app and team data; computers run bots with the team's
model subscriptions. The server runs no bots or model CLIs; it may call your configured Decision provider. Setup suggestions
from Tico HQ are on by default and share your answer and team description; [Privacy](../PRIVACY.md) lists the payload and controls.

## New owner: get one bot working

Try locally with Docker on a Mac with Docker Desktop running, or on Linux:

```bash
curl -fsSL https://github.com/ticoteam/tico/releases/latest/download/install.sh | sh -s -- --local --owner-email you@example.com
```

[Start here](install.md#quick-start-on-your-own-computer) continues through provider sign-in, **Set up**, a first draft and a completed task.
Use `releases/download/vX.Y.Z/install.sh` to pin a release. For a shared server, follow [Hosted install](install.md#install-the-server-for-your-team).

- [Build your team](onboarding-guide.md), [Team chart](org-chart.md) and [Humans](people.md).
- [Choose and set up bots](creating-bots.md), with the full [Starter catalog](starter-bots.md).
- [Connect tools](connect-tools.md), [Credentials](credential-vault.md) and [Permissions](permissions.md).
  Members can have 25 active bots by default; the owner can change the limit.
- Keep Tico running: [Health and recovery](install.md#operating-it), [Updates](updates.md),
  [Backups and restore](install.md#backups-and-restore) and [Support](support.md).

## Team member: use it every day

- [Use Tico](using-tico.md): your team's address, tasks, chats, results and Needs you.
- [Desktop app](desktop.md), [Your Assistant](assistant.md) and [External agents](connect-an-agent.md).
- Knowledge and results: [Docs](docs.md), [Librarian](librarian.md), [Files](files.md), [Meetings](meetings.md), [Listening](listening.md).
- [Goals and KPIs](goals-and-kpis.md), [Humans](people.md), [Groups](org-chart.md) and [Access](permissions.md).
- [Glossary](glossary.md) and [Help](support.md).

## Developer or computer operator

- [Register a computer](install.md#add-computers-to-run-your-bots), [Harnesses](harnesses.md) and [Environments](environments.md).
- [Build a bot](creating-bots.md), [Routines](routines.md), [Agent context](agent-context.md) and [Watchers](watchers.md).
- [API](api.md), [Custom frontend](custom-frontend.md), [Listening API](listening.md#save-decide-and-resolve),
  [Needs you batches](needs-you-batches.md) and [Service keys](service-keys.md), for another system that files tasks.
- External agents: [Common setup](connect-an-agent.md), [Hermes](hermes-agents.md), [OpenClaw](openclaw-agents.md), [Grok Bot and Dots](external-agent-sync.md).
- Advanced operations: [Cloud provisioning](install-advanced.md), [Sizing](sizing.md), [Observability](observability.md),
  [Databases](databases.md), [Query Tico data](hub-sql.md), [Slack gateway](slack-gateway.md).
- Project maintenance: [Architecture](architecture.md), [Contributing](../CONTRIBUTING.md), [Releasing](releasing.md),
  [Tico HQ](tico-hq.md) and [Telemetry](telemetry.md).

[Task types and steps](tasks.md): custom pipelines, status mapping, and CLI, MCP, API and SQL examples;
editing and deleting your own comments.
