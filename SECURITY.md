# Security policy

## Reporting a vulnerability

Please report security problems privately. Do not open a public issue or pull request.

Use GitHub's private vulnerability reporting: go to the
[Security tab of ticoteam/tico](https://github.com/ticoteam/tico/security/advisories/new) and
choose **Report a vulnerability**. Include what you found, how to reproduce it, and which
version or commit it affects. Only the maintainers can read the report.

If you cannot use GitHub for this, open a public issue that says only that you have a security
report and asks for a private way to send it. Put no details in it, and we will reply there with one.

Never put a real credential in a report, an issue or a pull request.

## Supported versions

Only the latest release gets security fixes ([releases](https://github.com/ticoteam/tico/releases)). Older
releases do not; the fix is to update, which a server does with **Update now** and a computer does by
itself ([docs/updates.md](docs/updates.md)). Tell us which version you tested; if the problem is
already fixed in the latest release, we will say so.

## What to expect

- We aim to acknowledge a report within 5 business days, and to tell you whether we consider it a
  vulnerability, and how we plan to fix it, within 14 days of that.
- A fix ships as a new release, with an advisory on GitHub that credits you unless you would rather
  not be named. We ask that you keep the details private until it is out; if a fix is taking long we
  will agree a date with you.
- This is a small project run by a few people. These are aims, not a contract, and we will tell you
  if we are slower.

## Scope

Tico's server, runner, `hub` CLI, MCP tools and desktop shell in this repository. Problems in a
third-party dependency are best reported to that project, but tell us if Tico's use of it makes the
problem worse.

## Threat model, in plain words

Tico has a small server and one or more computers that run bots. What each one holds decides what
losing it costs you. See [docs/architecture.md](docs/architecture.md) for the picture.

### The server

The server holds the team's data: humans, bots, tasks, chats, approvals, routines, files, meeting
notes, and the shared credential store (encrypted with the key you configure). It never runs a bot or a model CLI, so it holds no bot model sign-ins and no bots' working files.
When decisions (or Slack routing) are on, it sends the text of each question to the decision provider you configured
(the optional hosted decisions service, or OpenAI, Anthropic, Gemini, xAI or OpenRouter with a key stored on the server). That text can include email.
Someone who takes over the server in that case can read the stored provider key and use it, and can see the
questions as they are sent. It does hold the sign-in
credentials (OIDC client secret, session secret) and, if you connect GitHub, the GitHub App's private key.
Stored secrets are sealed at rest, never kept in plaintext in the database: credentials and support ticket secrets with
the vault's data key; the GitHub App, directory sync and Slack secrets with the vault's KMS-wrapped key, or without KMS
with `github-app.key` and `slack.key`; and an agent credential waiting for a paired profile to collect it, with a key
only that profile's pairing secret opens ([Shared credentials](docs/credential-vault.md#other-secrets-the-vault-seals)).
Someone who takes over the server can read and change team data and can mint GitHub tokens, so
treat it like any internal application server: patch it, keep the data volume private, and restrict
SSH.

### A runner computer

A computer that runs bots holds: the model sign-ins for the harnesses installed on it (Codex, Claude
Code, Gemini CLI and so on), the credentials its bots need (`secrets/` in the workspace), and the bots'
git repositories with their instructions, knowledge and memory. Someone who takes over that computer
has all of that, plus whatever the OS account can reach. A runner's credential lets it claim work for
the bots assigned to it; it does not give it team-wide authority on the server. Revoke a lost
computer in **Settings > Computers**.

### Bots on one computer share a trust boundary, on purpose

Tico shares workspace files and model sign-ins between bots on the same computer. Except for the Docker turn
users described below, they run as the same OS user,
in the same workspace, with the same model sign-ins. The runner delivers only credentials granted to that bot,
not the whole environment or `_shared.env`; upgrades automatically grant each bot the shared credentials it already uses.
This delivery filter is not OS isolation: bot code can still read another bot's files and credentials on that computer. This is a design choice, not a bug: it keeps setup simple and lets
bots on a team cooperate.

What is separated: the computer's own credential. The runner's registration (`runner.json`) can claim the
work of any bot assigned to that computer, so code a bot runs must not be able to read it.

- **Docker runner** (`docker/runner.compose.yaml`, the installer's default): the supervisor keeps the runner's
  own user, which owns `runner.json` (mode 0600), its state and the tools directory, and runs every process
  that executes bot code (the model CLI of a run, `git` in a bot's checkout, sign-in flows) as the unprivileged `bot` user. A
  run gets its own run token and the environment the runner passes it, nothing of the registration.
  Its GitHub token comes from a local socket that answers only "a token for the bot this run belongs
  to", given the run token; the registration never crosses it. Authenticated repository fetches use
  supervisor-owned bare mirrors with read-only access for bots (0755 directories, 0644 files). The state
  directory allows traversal (0711) so bots can read mirrors; its existing private files remain 0600 and
  private subdirectories 0700. Base clones refresh from mirrors as the bot user, without a token.
  **Gateway exception:** each Tico model harness (primary or fallback) leases a distinct OS user, sharing the bot
  group for workspace access. Sibling bot processes cannot read its live process environment, file descriptors or
  private turn directory. The relay checks the connecting socket's UID before reading its per-turn bearer token.
  Non-Gateway hosts are never given that relay token. The runner stops the relay and all of its calls, kills the
  leased user's descendants, removes private state and temporary files, and returns workspace ownership to the
  ordinary bot user before returning the lease. Startup sweeps crash leftovers; failed or incomplete cleanup
  quarantines a UID instead of reusing it. The pool holds up to 64 concurrent leased turns.
  **Turn users for other harnesses:** a turn on a harness whose manifest sets `isolated_user` (pi, which reads its
  key from the turn's environment) leases a user the same way, with a private HOME, temporary directory and copies of
  its file credentials. Every checkout and worktree root has a group of its own (owner `bot`, mode 0770, and an ACL entry for the runner's
  own user, whose git reads checkouts), and a leased
  user gets only the groups of the checkouts its turn runs in, so it cannot enter or write another bot's checkout or
  task worktree. The folders that hold checkouts (the workspace, `sessions`, each bot's lane, `tasks`, `repos`) are
  sticky, so it cannot rename another bot's checkout aside and put its own in its place. Readiness reports per tool whether its turns run as their own user (`turn_user`), and the
  computer's tool chip says so.
- **Not separated on the Docker runner, by design for now:**
  - Harnesses that keep owner-only sign-in state shared across turns: Claude Code (`~/.claude/.credentials.json`,
    `~/.claude.json`), Codex (`~/.codex/auth.json`), Gemini CLI, Cursor, Grok, Muse Code and Antigravity. Their
    sign-in files are 0600 and rewritten by the CLI, so another user could not sign in. Their turns run as `bot`,
    which owns every checkout and can enter any of them.
  - The runner's own git (publishing, refreshing checkouts) runs as `bot` or the supervisor and honours the
    repository's config.
  - Shared base clones (`workspace/repos/<base>`), which every computer task worktree of that base uses.
  - Watchers and repository setup commands, which run as `bot`.
  The brokered sign-in implementation is gated on the Docker isolation test before either Claude Code or Codex
  advertises `isolated_user`; both remain in the residual list above until that gate passes. Its supervisor-owned,
  per-login broker alone receives refresh credentials. Codex uses an ephemeral, access-token-only app-server login
  in a private `CODEX_HOME`, with on-demand refresh through the broker. Claude Code receives
  `CLAUDE_CODE_OAUTH_TOKEN` in a private `CLAUDE_CONFIG_DIR` only when its remaining lifetime covers the turn;
  the runner ends the turn before that credential expires. Neither copies refresh credentials into a turn home.
  Only refresh calls share a lock: subscription turns and API-key turns remain concurrent. Per-conversation
  transcripts return through a bounded, link-refusing supervisor store; sign-in and configuration files are excluded.
  The automatic capacity ceiling is 64, matching the leased-user pool; an explicit larger capacity queues for a user
  rather than weakening isolation. Gemini CLI, Cursor, Grok, Muse Code and Antigravity remain on the shared bot user.
- **Mac runner:** one user runs the runner and the bots, so the registration sits in the same account the
  bots run as: treat the Mac as one trust group. A second macOS account for the bots is possible by hand but
  not built in.
- **Still shared, by default:** the `bot` user's workspace, credentials and model sign-ins, so one bot can read
  another's repository, shared credentials and subscription sign-ins. Turns that run as `bot` still share process
  identity and environment access. Leased turn users protect live turn state and keep a leased turn out of other
  bots' checkouts, not the shared sign-ins; separate computers or separate containers remain the boundary for unrelated trust domains.

Practical guidance:

- Keep team-wide credentials (a Google service account with domain-wide delegation, team-wide
  tokens, admin keys) off computers whose bots read untrusted email, web pages, or documents. Text
  from outside can try to steer a bot (prompt injection), and a steered bot can reach anything on its
  computer.
- Group bots by trust. Put bots that handle untrusted input on their own computer, and bots that
  hold powerful credentials on another that reads nothing untrusted.
- For unrelated trust domains (two teams, or groups that must not see each other's data), use
  separate computers, or at minimum separate OS users, each with its own environment and workspace.
  `docs/environments.md` describes the isolated mode with one OS user per environment.
- Give each bot only the credentials it needs (a per-bot file, not the shared one), and use approvals for
  actions that send, spend or delete.

### Sign-in

Humans sign in with the built-in Google or Microsoft (OpenID Connect) sign-in by default. The server
verifies the token, then requires the email to be on the roster; an account that is not on the
roster is refused. You can restrict sign-in to your email domains. Cloudflare Access and AWS ALB with
Cognito are supported as advanced front doors you run yourself: the server verifies the signed JWT
they pass, so the proxy must be the only way to reach the server. Loopback sign-in with an owner token
exists only for a local-only server and is refused on a public URL. The owner controls who has access;
protect the owner's account with your identity provider's MFA.

### Who can see, read and write to a bot

Once someone is signed in, each bot decides three things separately: who may **see** it (the team chart and
bot lists), who may **read** its activity (tasks, updates, files, status, run log, routines, shared rooms) and
who may **write** to it (messages, tasks, notes, comments that wake it). Each is everyone, or chosen humans,
groups and bots, set per bot in Settings > Bots; a new bot starts open to everyone. The owner, the bot itself,
the humans above it on the team chart and bot administrators for their own bots always have full access. The
server decides it in one place for every route, for a personal API token and the MCP tools as much as for the
web app, and cuts lists and SQL results in the query so counts and pages do not leak; a bot you cannot see is a
`404`. See [docs/permissions.md](docs/permissions.md). The old `private_owners` and `routing_permissions` lists in
`hub-access.yaml` no longer restrict anything: an install that used them is Open after the upgrade until its owner
sets access, and its Health page says so. Before this, a task, comment or note to a bot needed no more than
seeing it, and a private bot was hidden from lists but not from every route that wrote to it; those gaps are closed.
Access is about what humans and bots may ask of a bot and read from it. It is not a boundary between bots that
share a computer (see above).

### Roles, members' bots and BotOps

Team roles are Owner, Admin and Member ([docs/permissions.md](docs/permissions.md)). Members may create bots (up to a limit),
own them and add coworkers in the team's email domain; admins manage every bot except the built-in ones (the Assistant, BotOps,
the Librarian and the CEO Bot are the owner's alone), humans and computers; only owners make admins. Admins are credential administrators by default, so a team gets going without the owner:
the shared credential vault belongs to the owner and the Admins, or to the owner and `TICO_CREDENTIAL_ADMINS` when the server names them, and
the owner can turn "Admins store credentials" off. Because bots on one computer are not isolated from
each other (above), a bot a member created goes only on its member's own computer or one an admin has opened to members' bots (never
another member's), and setup never places one elsewhere. Credential delivery follows each bot's grants; an admin's placement
of a member's bot on a closed computer through BotOps still needs their own click. SQL shows a member the
`events` that are their own or concern what they may read, no roster or sign-in records, and no goals of bots they may not read.

BotOps builds bots for humans by acting as the human whose own chat message started its run: checked with their rights,
recorded "via BotOps", and never for a message a bot or the Assistant wrote, a message routed from Slack, words inside a task or
document, or a message over a week old, since any of those can carry injected instructions. A message cited by id must be the
requester's own, in their own room with BotOps, within a day. BotOps holds no authority of its own over other bots: routines and
quarantine follow the requester's management of the bot too. Adding humans (outside the domain needs an owner or admin), deleting
bots and their repositories, updating Tico, and the owner's changes to Team rules run directly when asked. Credential administrators
and holders delegating to bots they own or run grant credentials directly. Making an admin, granting add_people, changing a human's
email or group, and placing a member's bot on a closed computer still produce Confirm cards. A bot drafts outbound messages until
sending to outsiders is turned on for it. [Permissions](docs/permissions.md) lists the remaining cards and the requester's required rights.

### GitHub tokens

With a GitHub App connected, Tico stores no long-lived personal token. For each run the server
requests an installation token that expires within the hour and is limited to that bot's own repository
plus any extra repositories the owner granted it. Installing the app on "All repositories" is the
recommended setup (new bot repositories work without a change) and does not widen a bot's reach, because
the token is scoped per bot. The app is private and asks for no workflow permission by default. Its webhook
(`POST /api/v2/github/webhook`) receives installation, pull request, review, check and push events and is checked
against the app's webhook secret; a request that fails the signature is refused. See [docs/github-app.md](docs/github-app.md).

### The updater and the Docker socket

The optional one-click updater container mounts the Docker socket so it can pull a new image and
restart the server. Access to the Docker socket is equivalent to root on that host. The tradeoff is
convenience against exposure: the updater only listens for an owner-initiated request authenticated by
a token, and you can omit it and update by hand ([release installer](docs/updates.md#manual-server-update)). Leave
it out if the server host runs anything else you care about.

### Backups

Litestream replicates the SQLite database continuously to a bucket you choose, and the data volume can
be snapshotted. A backup contains everything the server holds, including the encrypted credential store
and sign-in credentials, so give the bucket a private policy, encrypt it and restrict who can read it.
In local-key mode, protect the copied keys too: automatic backups include `credential-key/credential.key`,
`credential-key/github-app.key` (GitHub App and directory sync secrets) and `credential-key/slack.key` (Slack tokens) in the
same destination as the database, and hand-run and scheduled bundle backups carry them beside their database copy, so
read access to the backup bucket opens those secrets unless KMS is on: keep backups in a separate bucket or prefix with
its own access.
Anyone who reads both can decrypt those secrets. A restore without the keys cannot decrypt them.
Separate key storage requires a custom backup/restore process; with `TICO_CREDENTIAL_KMS_KEY`, protect access to the KMS key instead
([Shared credentials](docs/credential-vault.md)). Bot repositories and computer workspaces are not part of the
server backup; they live in Git and on the computers.

Never put a credential in git, in a task, or in bot instructions.
