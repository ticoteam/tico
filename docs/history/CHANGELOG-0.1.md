# Changelog 0.1

Releases in the 0.1 minor, moved out of [CHANGELOG.md](../../CHANGELOG.md) when 0.4 took its place. Each section is the notes its GitHub release carries, word for word.

## 0.1.0 - 2026-09-29

First public release.

### Added
- Server: one company per environment, holding people, bots, tasks, chats, approvals, routines,
  meetings, files and an audit trail in SQLite, with the routine scheduler built in.
- Runner: executes AI employee bots one leased attempt at a time on a company machine, through the
  model CLIs the company is already signed in to, so credentials never leave that machine.
- Web interface and desktop app (macOS, Windows, Linux): the org chart, Updates (the bots' daily
  and weekly reports), Tasks, chat with bots, Needs you approvals, Integrations, Settings and help.
- Bots: created from templates in their own git repositories, with a first-run onboarding wizard
  and BotOps, the bot that sets the others up.
- Shared hub over MCP and the `hub` CLI, so any agent can use the same tasks, messages and
  company context as people.
- Connectors for mail and calendar, meetings, Slack and GitHub, plus a credential vault.
- Sign-in through an identity-aware proxy (Cloudflare Access or AWS ALB with Cognito), or loopback
  sign-in for a local install.
- Hosting: local only on a Mac, or self-hosted, including a reference AWS stack under `infra/ec2/`
  with Litestream backups.
