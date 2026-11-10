# Architecture

Tico is a small server plus any number of computers. The server keeps the team's state. The
computers run the bots. They only ever talk to each other over HTTPS, and the computer always
starts the conversation.

```
   humans (browser or desktop app)
        |  HTTPS, Google / Microsoft sign-in
        v
  +---------------------------------------------+
  | SERVER (Docker)                             |
  | Caddy or Cloudflare Tunnel -> Tico server   |
  | SQLite + Litestream backups, files          |
  | tasks, chats, routines, humans              |
  | optional updater                            |
  | runs NO bots; sends questions to the        |
  | decision provider, when that is on          |
  +---------------------------------------------+
        ^                    ^                   ^
        | HTTPS: claim work, stream results, heartbeat
        |                    |                   |
  +-----------+        +-----------+       +-----------+
  | Mac       |        | Linux     |       | cloud VM  |    each joined with a
  | runner    |        | tico-     |       | tico-     |    one-time code
  | (native)  |        | runner    |       | runner    |
  +-----------+        +-----------+       +-----------+
   harnesses (Codex, Claude Code, Gemini CLI, Grok, catch-all),
   local model logins, bot repos, granted run credentials
        |                              |
        v                              v
  model providers                 GitHub (per-team App,
  (OpenAI, Anthropic, Google,      token scoped to each bot's repo)
   xAI, others)
```

## What runs where

| Where | What |
|---|---|
| Server | The web app and API, sign-in, the human roster, tasks, chats, the routine scheduler (the clock), files, the credential store, Litestream backups, and the optional updater. |
| Each computer | The runner, the harnesses installed on it, the workspace with one `bot-<slug>` git repository per bot, Credentials delivered for active runs, and local model logins. |
| Model providers | Bots reach them from computers, using that computer's logins. The server reaches only the decision provider, when decisions or Slack routing are on. |
| GitHub | Reached from computers with short-lived tokens the server mints per bot. |

A bot is one git repository plus one row on the server, assigned to one computer. Harnesses are
installed on a computer when a bot there needs one; the server does not need to know how.

## How a bot run flows

1. Something creates work: a human files a task or sends a message, a routine comes due (the server
   turns it into a task), or another bot hands work off.
2. The server queues it for the bot and the computer that bot is assigned to.
3. That computer's runner, heartbeating every few seconds, claims the work with a lease. A lease
   expiring does not prove the old process stopped: each claim includes optional `busy_bots`,
   listing bots whose turn processes are still running on that computer. The server skips those
   bots there until their processes exit, even if their leases have expired. Older runners omit
   the field; older servers keep their existing claim behavior.
4. The runner asks the server for a scoped GitHub token if needed and starts the harness inside
   the bot's repository.
5. The harness talks to the model provider directly, using the login on that computer, and uses tools
   (`tico` commands, git, shell) to do the work.
6. The runner streams progress and the reply back to the server. A bot that needs a person asks on the task and its
   run ends; the answer wakes it again. The bot commits what it learned to its repository.
7. The server records the result. What needs a person is in their Needs you.

If the computer is asleep or offline, the server keeps accepting work and shows the computer as
offline; the work waits and runs when it returns.

## Why the server runs no bots or model CLIs

The server never runs a bot or a model CLI; when decisions (or Slack routing) are on, it sends the text of each question to the decision provider you configured (the optional hosted decisions service, or OpenAI, Anthropic, Gemini, xAI or OpenRouter with a key stored on the server).

- **Local model logins stay on computers; Credentials are held by the server.** Subscription sign-ins
  for model CLIs stay on the computer where you signed in. Tico's server stores encrypted bot
  Credentials and model keys granted to **Every computer**, decrypts them and delivers them to
  granted bots or computers. It also holds the Decision provider's key when configured. A compromised
  server can expose those stored values; protect the Credential key and the complete backup.
  See [Credential storage and recovery](credential-vault.md).
- **The server stays small.** It does bookkeeping, not inference or long-running agent processes, so
  1 to 2 GB of RAM has served a small pilot ([sizing](sizing.md)).
- **Bots run next to their work.** A harness needs a shell, git, build tools and the bot's files, which
  belong on a computer, not in a web server.
- **Any harness, any provider.** Adding one is a runner change; the server only sees runs and results.

## Related

[Install](install.md), [sizing](sizing.md), [security model](../SECURITY.md),
[how it works](how-it-works.md), [GitHub App](github-app.md), [environments](environments.md).
