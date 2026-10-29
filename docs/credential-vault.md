# Shared credentials

How a credential reaches a bot. A bot receives only its granted Credentials; a `tools:` entry names the
variable a tool needs. The runner retrieves the granted values and resolves 1Password references. It masks a run's
granted values (as typed, URL-encoded or base64) with `••••` in everything it posts and logs, in the text
files the run changed in the repository, and holds back a push whose commits contain one (`runner/redact.py`).
Text-file scrubbing opens regular files without following symlinks and replaces them atomically in the same folder.
Oversized files are left out of publication; scrubbing never writes through a swapped file or parent symlink.

## Who can use a credential

Four words, used the same way everywhere:

- A **human** signs in. The owner and the admins are the **credential administrators**: they store, delete and grant credentials
  (`TICO_CREDENTIAL_ADMINS` names a different list). A member is never one, but stores a credential for a bot they own or manage,
  granted to that bot, and shares it with their other bots (team rule **Members store credentials for their bots**, on by default).
- A **bot** is a worker. It has the credentials it was given and nothing else. **A bot never uses a credential that was not
  granted to it**, and never another bot's: having one in its own file on its computer does not make it anyone else's.
- A **credential** is a stored secret with a name and, for a bot, the environment variable it arrives in (`JIRA_BASIC_AUTH`). Its value
  is encrypted and is never shown in a message, a log, an event or to a model.
- A **grant** is the explicit yes: "this credential, for this bot" (or for a person). A grant can be taken away (**revoked**) and the
  bot's next run no longer has it.

## Tico's Credentials

Tools → Credentials lists team credentials, usernames and masked previews. The owner and admins administer the vault. Each credential has its own grants. A member who stored a credential for their bot sees it listed (name, variable, grants; no **Reveal** unless it is also granted to them) and gives it to, or takes it from, bots they own or manage, while it is still theirs ([permissions.md](permissions.md#bot-owners)); giving it to someone else's bot is refused with who to ask. A granted human may reveal/copy it or attach it to bots they manage; a direct bot grant works on its assigned computer during an active run. Revoking the parent grant removes delegated bot access. Changing a bot owner invalidates delegation from its former owner.

Credentials use AES-256-GCM with per-write random nonces and credential-bound authenticated data. Reveal operations are audited; credential values are excluded from audit and idempotency receipts. Restoring a snapshot revokes restored grants to avoid resurrecting permissions.

The Credential's variable-name field becomes an environment variable only for a granted bot, during its run (`GET /api/v2/credential-runtime`), on a Mac or Linux computer and in the Docker runner alike. File Credentials become mode-0600 temporary files during the run. A bot cannot be granted two Credentials that use one variable name (the second grant is refused until the first is taken away). Computer login entries describe existing CLI/browser sessions and must be connected separately on each computer. A bot's Tools list and Health count a granted Credential as present ("granted through the credential vault") even though its computer's secrets file does not hold it. Existing bots' own-file values migrate into grants on upgrade; an old file is never a run fallback. Storing a Credential does not erase that file or give its value to another bot.

Model credentials named `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `CLAUDE_CODE_OAUTH_TOKEN` or `CURSOR_API_KEY` infer
that variable when the optional variable field is blank. An explicit variable always wins. For a stored API key or token,
**Manage access > Every computer** lets computers sign their models in. It gives no bot access: grant a bot separately.
Older name-only model credentials also offer this choice; granting them fills the missing variable without changing the secret.

## The key: nothing to set up

During a server-first upgrade, the one-time import of legacy team `HUB_` variables waits for
the Computer to report runner 0.2.31 or newer. An older or unreported runner cannot consume that
step; existing grants keep working. The first import can finish on an older runner while this
later step remains pending.

The vault works as soon as Tico starts. The AES-256 data key is 32 random bytes made once, the first time a credential is stored,
and kept in `credential.key` (mode 0600, the server's user) in the data volume next to the database (`/data/credential.key` in the
Docker install). The database holds only encrypted values and a fingerprint of the key, so a copy of the database alone cannot read them.
The key is never logged or returned by any route.

**Automatic backups include `credential.key`** in the same destination as the database: the backup loop copies the file
that Litestream does not copy. Anyone who can read both can decrypt vault credentials, so protect the complete backup as a secret.
See [The local credential key and backups](#the-local-credential-key-and-backups). An additional copy in a password manager can help
recovery; separate key storage requires your own backup and restore process. A database restored without its key file cannot decrypt any credential: Credentials
says the key file is missing, and Tico never makes a new key over an existing vault, so nothing is lost by putting the file back.
Re-enter the credentials only if the key is truly gone.

To use AWS KMS instead, set `TICO_CREDENTIAL_KMS_KEY` (a key id or alias). Then the data key is wrapped by that KMS key and only the wrapped form
is in the database. Setting it on a server that has been using the key file wraps the same key with KMS on the next use, without
re-encrypting anything, and the file can then be deleted. An install that began with KMS is unchanged, and taking the KMS key away
again is refused rather than starting a second key.

In Docker, set the KMS key in the install's `.env` and run `docker compose up -d` to recreate the server.
The server container also needs AWS credentials and a region, with `kms:GenerateDataKey`, `kms:Encrypt` and `kms:Decrypt`
on that key. An instance role can provide the credentials. For an AWS credentials file, mount it read-only and set the SDK's
region in `compose.override.yaml` (keep the file private and outside version control):

```yaml
services:
  server:
    environment:
      AWS_DEFAULT_REGION: us-east-1
      AWS_SHARED_CREDENTIALS_FILE: /run/aws/credentials
    volumes:
      - ./aws-credentials:/run/aws/credentials:ro
```

**Settings > Health > Credential encryption** reports whether the data key is wrapped with the requested KMS key.
Before the first credential save, or until a local key is wrapped on its next use, it warns that KMS is not active yet.
If saving fails, check the container's AWS credentials, region and key permissions; existing encrypted values stay intact.

## Store a new Credential

Open **Tools → Credentials → Add credential**. Set the type, value and **Bot variable name**
(the exact variable its Tool needs), then save. In the Credential's **Access** dialog, choose
the bot under **Grant access to** and press **Grant access**. Storing alone does not grant it.
BotOps can instead request a Credential card in chat and store and grant it with your rights.
Use file import only to migrate a value already in an older install; never set up a new bot by
writing `secrets/<bot>.env` or `_shared.env`.

## Give a bot a credential another bot has

Say it in the chat with BotOps: "Give Engineering Monitor the Jira access Jira Manager has." BotOps acts as you. A credential administrator may grant it; the person who stored it may give it to a bot they own or manage; a holder may delegate a stored credential to a bot they own or run.
Importing from another bot's file still needs a credential administrator. File import is only for migration of an existing value. It runs at once, with no Confirm card,
and then tries the connection as the bot:

1. If the credential is only in the first bot's own file (`secrets/<bot>.env` on its computer), BotOps moves it into Credentials first
   (`hub credential import JIRA_BASIC_AUTH --from-bot jira-manager`). The computer that runs that bot reads the variable from
   that one file and sends it to the server itself, over its own signed-in channel. The value is not printed, logged or put in a
   message, and the file is not changed, so the first bot keeps working. Only a credential administrator can ask, and only for a
   variable in that bot's own file (not `_shared.env`, not another bot's).
2. It grants the stored credential to the second bot: `hub credential grant "JIRA_BASIC_AUTH" --to engineering-monitor`. (`--to` takes the
   bot's name or slug; the credential is named by its name or its variable.) The second bot has it, as that variable, from its next run.
3. To take it away: `hub credential revoke "JIRA_BASIC_AUTH" --from engineering-monitor`.

The same three steps are the tools `hub_credential_import`, `hub_credential_grant` and `hub_credential_revoke`, and Tools → Credentials
does the grant and revoke by hand. A grant to a person, or to every computer, still asks for the person's own click when it comes through BotOps.

## Import from a password manager

A credential administrator can bring logins over from Bitwarden or Vaultwarden (an unencrypted JSON export) or from KeePass
or KeePassXC (a CSV export): Tools → Credentials → Import, or `hub credential import --format bitwarden|keepass FILE`.
Each login keeps its name, username, password, authenticator key and the hostnames of its web addresses; an entry without a
web address or a password is left out and the reply says why. A login already stored with the same name, username and
hostnames is skipped. Imported logins are granted to no bot, so a bot gets one only when someone grants it. An encrypted
Bitwarden export is refused; export again with the unencrypted format.

## The local credential key and backups

Without `TICO_CREDENTIAL_KMS_KEY`, the data key is 32 random bytes in `/data/credential.key` (mode 0600) on the server's data
volume. The database holds ciphertext only, so **a database restored without that file cannot decrypt a credential**. Litestream
copies the database and not the file, so the backup loop copies the key to the same backup destination: the bucket and prefix in
`TICO_BACKUP_URL` (object `credential-key/credential.key`), else the `tico-backups` volume. It copies it when the key first
appears and whenever it changes, and never logs it or sends it anywhere else. The object is encrypted at rest as the bucket is;
because the database backup is in the same bucket, keep it private, limit its access key to it, and turn on versioning.

Health shows **Backups** as a warning while a local key exists, backups are set up, and the key has not been copied yet; with
backups only on this server the note says the key is lost with the server too.

To restore: `docker compose run --rm --no-deps server restore` brings back the database, attachments and the key (see
[Backups and restore](install.md#backups-and-restore)). By hand, copy `credential-key/credential.key` from the backup
location to `/data/credential.key` (32 bytes, mode 0600, owned by the server's user) before the server starts. Tico never makes a
new key over an existing vault, so putting the file back loses nothing. Setting `TICO_CREDENTIAL_KMS_KEY` replaces the file
as the thing to protect: the data key is then wrapped by KMS in the database.

Two more key files sit beside it and are copied and restored the same way: `github-app.key` (the GitHub App's private key,
client secret and webhook secret, and the directory sync credentials) and `slack.key` (the Slack tokens). They are not the
vault's key, so a restore needs all three ([Backups and restore](install.md#backups-and-restore)). Health's **Stored
secrets** check names any secret this server cannot open and the file to put back. Hand-run (`python -m
backend.manage backup`) and scheduled bundle backups carry all three as well.

With `TICO_CREDENTIAL_KMS_KEY` set, new GitHub App, directory sync and Slack secrets are sealed with the vault's
KMS-wrapped key. One sealed with `github-app.key` or `slack.key` before KMS was set still opens with that file, and is
re-sealed with the vault's key the first time it is read; until then Health's **Stored secrets** says how many still
need each file. Reading never makes a key file.

## Other secrets the vault seals

**Support ticket secrets.** Tico HQ answers a support request with a secret for that ticket alone; the server keeps it
sealed with the vault's data key (`support_tickets.hq_secret`, AES-256-GCM, bound to the ticket), never in plaintext.
On a fresh install the first ticket makes the vault's key, as the first credential would (`credential.key`, or the KMS
data key): it is then backed up like any key. When the vault cannot be opened (`credential.key` missing, KMS
unreachable), filing a ticket is refused with `vault_unavailable` before anything is sent to HQ, so no ticket is made
that this server could not keep. Secrets an older release kept in plaintext are sealed when the server starts; that is
idempotent, and when the vault cannot be opened at start they are sealed on the next start or the next use.

**A pairing's agent credential is not in the vault.** From approval until the Hermes or OpenClaw profile collects it, the
minted credential waits in `agent_pairings.token` sealed to a public key derived from the pairing secret, which only the
profile holds (X25519, HKDF-SHA256, AES-256-GCM). The server keeps the secret's hash and that public key, so neither the
database nor the server can open it without the profile's next poll. It is wiped at the first heartbeat, or when the
pairing expires.

## Credentials asked for in the chat

A bot that needs a credential opens a **credential card** in the conversation where it asked (`hub credential request <VARIABLE> --for-bot <bot> --label
"your Jira credential" --format "you@example.com:API token" --help-url https://...`): the title says what it is for, the input shows the exact format,
"Get one" opens the page where the token is made, and Save sends the value from the browser to `POST /api/v2/credential-requests/{id}/save`.
The server checks its shape (a `:` where the format has one; never echoing the value), stores it in this vault under the variable's name
(a credential already holding that name for that bot is replaced, one shared with other bots is left alone), grants it to that one bot, and wakes the
asking bot with "Saved". The value is in no message, event, receipt or log, and never reaches the model. Only the human who was asked, or a
credential admin, can fill a card. A credential admin stores any; a member stores one for a bot they own or manage, replacing only a
credential they stored themselves, unless the owner turns **Members store credentials for their bots** off in Settings > People; anyone
else sees who to ask.

A bot asking from a task (its turn works on one) puts the card in the task's thread instead, for the person who wrote to it
there, else the first of its operator and the company owner who can read the task (none: the ask is refused), and the
task waits on the card ([Reconnect cards](tasks.md#reconnect-cards)):
saving it wakes the task once with "<label> is saved. Continue." in place of the "Saved" message. BotOps asking on its own
task does the same, for the person it acts for. Asking again on the same task returns the open card; another task's open
card for the same variable stays (saving either saves both); cancelling the card ends the task's wait and tells the bot.
When the task finishes, changes hands, or is made private from the card's person, its card says Not needed and its
request is cancelled; a person who cannot read the task cannot open its request either.

If a human pastes a credential into the chat instead, BotOps stores it with `hub credential set <VARIABLE> --for-bot <bot>` (the value on standard input,
never on the command line) as that human, and the server takes the pasted words out of their messages, the run's recorded events and the answers kept
for retries, replacing them with `•••• saved as <VARIABLE>`; later events of the same run are scrubbed as they arrive. The runner masks granted values in
what it posts and logs. `hub message redact <id>` does the same for one message.

## Website logins

A **login** is a credential for a website a bot's browser signs in to: a username, a password, an optional 2FA setup key
(the base32 key a site shows under "can't scan the code?", or the `otpauth://totp/...` link in its QR code), and the
sites it is for. Sites are lowercase hostnames (`app.acme.example`); a page on a listed host or any subdomain of one
matches. The password and the 2FA key are sealed together as one value (`{"password", "totp"}`); the username, the sites
and whether it has 2FA are plain columns, so Credentials lists them (`sites`, `has_totp`) without opening the vault.

A login is never an environment variable, and a bot never holds its password or 2FA key: the turn's own commands
can read whatever the turn holds, its hub token included. Only the bot's runner reads it, with its computer token, from
`GET /api/v2/runner-login-credentials?attempt=<run id>` (`logins`: id, name, username, sites, password and 2FA key,
for the logins granted to that run's bot, while the run is live on that computer), to type it into the page with
`hub browser fill` and to mask it in what the run records; the model asks for a field and never sees the value. Each
send is recorded as `credential.sent_to_computer` (`use: "browser"`), never with the value. A bot token, an API token,
BotOps acting for someone and a person are refused there, and `GET /api/v2/credential-runtime` leaves logins out.

A person who may open a login (a credential admin, or someone it is granted to), signed in themselves, may reveal its
password (`POST /api/v2/credentials/{id}/reveal`, recorded as `credential.revealed`) and see its current 2FA code with
**Show code** (`GET /api/v2/credentials/{id}/code`: `code` and `expires_in` seconds; recorded as `credential.code`,
never with the code). The 2FA key itself is never returned by any route. A bot, an API token or BotOps acting for
someone is refused both.

Add a login in Tools > Credentials, or with `POST /api/v2/credentials` `{"kind": "login", "name", "username",
"password", "totp", "sites"}` (credential admins; `secret` is taken for `password`; a blank name is the first site). On a
change (`POST /api/v2/credentials/{id}` with `expected_revision`), a field left out, or a blank password, stays as it
was, and `"totp": ""` takes the 2FA key off. Saving a login whose sites cover a host a bot holding it waits on closes
that wait. A login stays a login: it has no variable to become.

A bot that needs a login asks for one on a card: `hub credential request --kind login --site app.acme.example
[--username ana@acme.example] [--label "the Acme portal"]`. The card takes a username, a password and an optional 2FA
setup key and saves them (`POST /api/v2/credential-requests/{id}/save` `{"username", "password", "totp"}`) as a login
granted to that bot: the one it already has for that username and a site in common is replaced, keeping its 2FA key
unless a new one is typed; otherwise a new login. On a task the card is a reconnect card on `site:<bot>:<host>`, and the
same rules as above hold for who may save it.

When a site wants something only a person has (a login the bot was not given, a captcha, a code sent to a phone), the
bot runs `hub browser needs-you <host> --reason "log in"` (`POST /api/v2/browser/needs-you`, on its current task). Its
task waits on a website card in the task's thread and in the fixer's Needs you: "Ops Bot needs you on app.acme.example:
log in". The card's `fix` names the `host`, the bot's `computer` (to open its screen and take over the browser), the
`reason`, and a login card for the host (`request`). It closes when the person presses **Done**
(`POST /api/v2/reconnects/{id}/done`, `id` being the card's `id` in its refs, its message; or a body `{"task", "key"}`), or when a login covering that host is saved for
or granted to the bot; either way every task of that bot waiting on the host is woken once.

## Credential files on the computer

Bots receive only their granted Credentials. A run does not inherit the runner's process credentials, `_shared.env`,
its old bot file, or a profile file. Revoking a grant takes the value out of the next run, even when an old file still has it.

On upgrade, each existing bot automatically gets grants for everything it could read before: its own file's variables,
every key in its computer's `_shared.env` (except the Codex sign-in key), the variables its Tools name, and the key used
by its model runtime. The Computer encrypts those values into Credentials through its own signed-in
channel. A bot created after the upgrade inherits no shared tool credentials. Migration is once per existing bot, and
never restores a revoked grant. A pending one-time migration waits for a runner version that supports
it; an older, pinned or offline runner does not consume the step. The `HUB_` migration needs a
runner reporting 0.2.31 or newer. Legacy files stay available to operator tools, but are no longer a source for run environments.
On isolated Computers, the supervisor owns these files. A Computer without process isolation still has the shared
filesystem trust boundary described in [SECURITY.md](../SECURITY.md); environment filtering does not isolate its shell.
Use Credentials or its chat card for new values; grant them to the bot that needs them.

## 1Password references

A stored Credential may be `op://vault/item/field` instead of the credential. The runner
resolves it at run start with a read-only 1Password service account (`OP_SERVICE_ACCOUNT_TOKEN`
on the Computer, `runner/op.py`). The service account token stays with the runner; only the resolved granted value reaches the bot. A reference that does not resolve becomes an
empty value, so preflight shows it as missing instead of a run failing halfway. Rotating an item
in 1Password reaches a reference on the next run.

`scripts/vault-sync.sh` is the by-hand complement: it copies plain values from 1Password into
legacy credential files for migration or service jobs outside bot runs. It does not grant a bot access;
store new bot values in Credentials and grant them instead. Its map
(`secrets/vault-map.txt`, or the file `VAULT_SYNC_MAP` names) has one line per variable,
`ENV_VAR | op://<vault>/<item>/<field> | target env file`; the target defaults to
`_shared.env`. Keep the map private; it names your vault items. An item whose name contains `@`
breaks the `op://` form; reference it by its item id.

## Rules for bots

- Credentials never go in tasks, git, logs, updates or prompts. A key-shaped string in a
  draft fails lint for this reason. A bot that needs one opens a card in the chat; BotOps stores what a human
  pastes and removes it from the conversation.
- A missing credential is not yours to work around: open the card (`hub credential request`); on a task it goes in the
  task's thread and the task waits for it, so stop there. The bot's Tools list shows every declared Credential as present or missing.
- Saving a credential is not permission to use it. Only a `tools:` entry and a grant connects a bot to a credential. Changing `tools:` is a task for the owner.
- A granted value exists only for that run; do not copy it anywhere that outlives the run.
- Each bot can have only one active granted Credential for a variable. Revoke its previous grant before replacing it.
- `can:` describes intended operations; restrict the vendor key or endpoint to enforce read-only access. Tico does not filter a vendor's MCP tools by `can`.
- Store new values in Credentials and grant them to the bot; a shell export does not grant access.

## Delete a Credential

A Credential administrator can choose **Delete** in Credentials, use `hub credential delete <name>`, or call
`DELETE /api/v2/credentials/<id>`. Deletion erases the encrypted value and all grants together. History records names and
variable names only. Revocation removes access while keeping the stored value; deletion removes the Credential itself.

**Every computer (signs models in)** is available only for stored model API keys and tokens whose explicit or inferred variable is
`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `CLAUDE_CODE_OAUTH_TOKEN` or `CURSOR_API_KEY`. Those names infer a blank variable;
older name-only keys fill the field when granted. This signs the model software in; tool Credentials still need a grant per bot.
`hub_credential_grant` targets one bot; use Credentials to grant a model key to Every computer.
