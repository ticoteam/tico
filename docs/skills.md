# Skills directory

Library › Skills is the company's list of approved skills: who has each one, and which version. A skill is a folder of text
files with a `SKILL.md` at its root (`skills/<name>/` in a bot's repository, or `.claude/skills/<name>/`). Nothing reaches a bot
or a person until it is installed explicitly.

## Propose a skill

Anyone who is a person or a bot can propose one. From the computer that has the folder:

    hub skill submit <skill> [--path DIR] [--note TEXT] [--base N]

The folder is read from `--path`, else the repository's `.claude/skills/<skill>` or `skills/<skill>`, else
`~/.claude/skills/<skill>`. The whole folder is sent. A bot can ask for it in words ("submit this skill to Tico"), or call the
`hub_skill_submit` tool. The same checks run on the computer and on the server:

- The name is lowercase letters, digits, dots and hyphens (up to 80 characters).
- Text files only, UTF-8, no symlinks, no `..`, no absolute paths. At most 100 files and 90,000 characters in all.
- `SKILL.md` at the root. If its frontmatter has a `name`, it is the skill's name. The description is at most 1024 characters.

A folder that breaks a rule is refused with the reason; nothing is truncated.

An update to a skill the directory already has is proposed against its latest version, unless `--base N` names another. The
review shows the diff against the version it was based on, and against the latest when the directory has moved on.
Proposing the exact bundle that is already the latest version is refused (`unchanged`). A new proposal from the same person
for the same skill replaces their pending one.

## Screening and approval

Every new skill and every update is screened before it can be approved. An admin approves or declines it. Approval is one
step against the exact bundle that was screened and the version it was based on. If either has changed since, approval is
refused until it is screened again (`not_screened`, `stale`). A high-risk or unsafe screen needs the admin to acknowledge it.
Approval makes the next version of the skill. Declining takes a reason of up to 300 characters.

## Installs

An install is a row: one skill, one bot or one person, and either the latest version or a pinned version. Installing for a
bot needs write on that bot. Installing for a person needs that person or an admin. A bulk install writes one row per target
and nothing applies to people who join later.

The install list shows two things: what was asked (`desired`: the version the install points at) and what the runner or the
skills helper reports as staged (`staged`: the sha it actually has). A difference between them is shown as is.

## The Skills page

Library › Skills (`#/library/skills`) has three tabs.

- **Directory**: every approved skill with its version, installs, uses in the last 30 days and when it was last used. A skill opens
  its page: versions (when and by whom each was approved), the files of any version, **Install** (for yourself, a bot you can write,
  a person if you're an admin, or a team: a team installs for its current members at once, and nobody who joins later), and its
  usage over 7 or 30 days by trigger (model, person, nested), by who and the last 20 uses.
- **Proposals**: admins see every proposal, anyone else their own. Each shows new or update, who proposed it and its screen:
  Pending, Screened with its risk, or Not screened with **Re-run**. A proposal opens its screening (summary, findings by file and line,
  pattern hits), its diff (or its files when it's new) and a Stale flag when the skill moved on. **Approve** for a high-risk or
  unsafe screen needs the "I've read this" tick, as the server does (422 `acknowledgement_required`). **Decline** asks for a reason.
- **Installed**: your installs and those of the bots you can write: Latest or pinned (with the newer version when there is one),
  what is wanted against what is staged there (short sha), and **Remove**.

**Use in my Claude Code** shows the one line that sets up the skills helper on your computer, with Copy:
`curl -fsSL --create-dirs <your Tico>/api/v2/skills/helper.mjs -o ~/.tico/skills-helper.mjs && node ~/.tico/skills-helper.mjs setup`.

Usage. Each use is one row (skill, who, trigger, where, when) from the runner, the skills helper or a typed `/name`. The hourly
**Skill usage** job folds them into daily counts and deletes raw rows older than 90 days; the daily counts stay. The page counts
days before the job's last run from the daily counts and the rest from the raw rows, so a use shows at once. Admins see who used a
skill; anyone else sees the totals and their own uses (and the ones they asked a bot for). A use links its task or thread only for
someone who may read it.

Coverage. A skill's installs say **Used**, **Unused** or **Unknown**. Unused needs data: the target reports staging (a receipt),
reports usage (a bot always; a person through a skills helper with reporting on), has had the install for 30 days, and has no use
in those 30 days. Anything else is Unknown, never Unused. Skills used on computers that aren't in the directory aren't collected,
so the page has no "popular, not in the directory" list.

## API

Under `/api/v2/skills`: the directory (`GET skills`), a skill with its versions and installs (`GET skills/{name}`), a version with
its files (`GET skills/{name}/versions/{number}`), proposals (`POST skills/proposals`, `GET skills/proposals`,
`GET skills/proposals/{id}`, and `approve`, `decline`, `withdraw`, `rescreen` under it), and installs (`POST skills/{name}/installs`,
`POST skills/{name}/installs/bulk`, `GET skills/installs?target_type=&target_id=`, `DELETE skills/installs/{id}`), and an approved
version's files by sha (`GET skills/bundles/{sha256}`). The Skills page reads four more: usage per skill
(`GET skills/usage?days=7|30`), one skill's usage (`GET skills/{name}/usage?days=7|30`), who the caller may install for
(`GET skills/install-targets`) and the caller's own installs with those of the bots they can write (`GET skills/installed`).
Route words such as `usage`, `installed`, `install-targets`, `proposals` and `installs` can't be skill names. The contract is in
`docs/openapi/v2.json` under the Skills tag. How a runner stages installed skills on a Claude Code turn and reports them:
[Harnesses](harnesses.md#company-skills-on-a-claude-code-turn).

## Privacy

Events and logs name a skill, its version and sha, and who did what. They never hold a file's contents or a prompt. A proposal's
files are readable by its proposer and by admins. Usage rows hold the skill, who, the trigger, where (task or thread) and when: never a prompt, the
skill's arguments or a file.

## People's Claude Code

The skills installed for you also reach the Claude Code on your own computer. A small helper
connects it once; after that Claude Code keeps your skills up to date by itself, and, if you choose, tells Tico
which skills you use.

Needs Claude Code 2.1.238 or later, Node 18 or later, and Tico on an `https://` address (Claude Code sends the
sign-in header only to an https marketplace).

### Connect

```sh
mkdir -p ~/.tico && curl -fsSL https://tico.acme.example/api/v2/skills/helper.mjs -o ~/.tico/skills-helper.mjs \
  && node ~/.tico/skills-helper.mjs setup
```

Setup prints an address and a code and opens your browser. Check the code, tick **Share which skills I use** if you
want to, and press **Approve**. Setup then:

- keeps the device token in `~/.tico/skills.json` (mode 0600);
- adds a marketplace named `tico` to `~/.claude/settings.json` (`extraKnownMarketplaces.tico`, a `url` source with a
  `headersHelper` that runs `skills-helper.mjs auth-header`, and `autoUpdate: true`), leaving your other settings as
  they are;
- installs `tico-skills@tico` and each of your skills (`claude plugin install <name>@tico --scope user`).

A skill named `deploy` runs as `/deploy:deploy`, since Claude Code names a plugin's skills after the plugin.

| Command | Does |
|---|---|
| `setup [--server URL]` | Connect this computer |
| `sync` | Install, update and remove skills to match Tico, then tell Tico what is staged |
| `status` | Server, device, reporting, Claude Code version, skills |
| `remove` | Remove the marketplace and its plugins, the settings entry, and revoke the device |

### What keeps it current

The `tico-skills` plugin holds the helper and three hooks:

| Hook | Runs |
|---|---|
| `SessionStart` (startup) | `sync --quiet`, in the background |
| `PostToolUse` on `Skill` | `report --trigger model` |
| `UserPromptExpansion` | `report --trigger person` (you typed `/name`) |

Each skill is an `archive` plugin pinned to its zip's sha256 and declares no version, so an approved update or a new
pin reaches you at the next marketplace refresh. The marketplace sets `forceRemoveDeletedPlugins`, so a removed
install is uninstalled at the next session start even without the helper.

### Usage reports

Only when you ticked **Share which skills I use** (change it in Tico: `PATCH /api/v2/skills/devices/{id}`). A report
holds the skill name, a sha256 of the Claude Code session id, the tool use or prompt id, and the time. Never the prompt,
the skill's arguments, or any file. The helper reports only names from your last sync, waits at most 2 seconds, and
never fails or changes a session. A typed `/name` also makes Claude Code call the Skill tool; that call is not reported
when the same skill was typed in the same session in the last 30 seconds (the helper keeps the last typed name per
session under `~/.tico/skills-typed/`). Tico drops names it does not know and ignores a repeat of one session and tool use.

### Routes

All under `/api/v2`. The device token (`tico_sd_`, stored as a sha256) is you, but reaches only the routes marked
*device*; anything else answers 403, and a revoked token 401.

| Route | Who |
|---|---|
| `POST skills/devices/codes` `{label}` → `{device_code, user_code, verification_url, expires_in: 600, interval: 5}` | No sign-in; 10 an hour per address, 50 waiting in all (429) |
| `POST skills/devices/token` `{device_code}` → 428 waiting, 200 `{token, device_id, reporting_enabled}` once, 410 expired, denied or used, 404 unknown | No sign-in |
| `GET skills/devices/codes/{user_code}` → `{user_code, label, expires_at}` | A signed-in person |
| `POST skills/devices/codes/{user_code}/approve` `{reporting_enabled}`, `/deny` | A signed-in person, in the browser (not a token) |
| `GET skills/devices`, `PATCH skills/devices/{id}` `{reporting_enabled}` | The person |
| `DELETE skills/devices/{id}` | The person, an admin, or the device itself (*device*) |
| `GET skills/marketplace.json` | The person or *device*; a device also gets `X-Tico-Reporting: on\|off` |
| `GET skills/plugins/{name}/{bundle_sha}.zip` | Any signed-in caller or *device* |
| `GET skills/bundles/{...}` | *device* (served by the skills runtime) |
| `POST skills/devices/receipt` `{staged: [{name, sha256}], claude_version}` | *device* |
| `POST skills/usage` `{events: [{name, trigger: model\|person, session_key, tool_use_id, used_at}]}` | *device*, reporting on (else 403 `reporting_off`); 100 events a call |
| `GET skills/helper.mjs` | No sign-in |

The Assistant and Tico support never connect, change or revoke a device, and Query Tico data shows neither
`skill_devices` nor `skill_device_codes`.
