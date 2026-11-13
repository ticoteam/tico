# Skills directory

Library › Skills is the company's list of approved skills: who has each one, and which version. A skill is a folder of text
files with a `SKILL.md` at its root (`skills/<name>/` in a bot's repository, or `.claude/skills/<name>/`). Nothing reaches a bot
or a person until it is installed explicitly.

## Propose a skill

Anyone who is a person or a bot can propose one. From the computer that has the folder:

    tico skill submit <skill> [--path DIR] [--note TEXT] [--base N]

The folder is read from `--path`, else the repository's `.claude/skills/<skill>` or `skills/<skill>`, else
`~/.claude/skills/<skill>`. The whole folder is sent. A bot can ask for it in words ("submit this skill to Tico"), or call the
`tico_skill_submit` tool. The same checks run on the computer and on the server:

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

## Screening

The **Skill screening** job (`backend/skill_screening.py`) screens each pending proposal, a few each scheduler pass, oldest
first. It claims a proposal with a conditional update, so two passes never screen the same one. Each screen writes a
`skill_screenings` row and sets the proposal's `screen_status`. It is mandatory: there is no switch, and no other model
answers in place of Decide High.

- **Patterns.** Plain code checks every file and records each hit as `{file, line, pattern}`. It looks for hidden Unicode
  (zero-width, bidi and tag characters), HTML comments, long base64 or hex blobs, and URLs (each one listed). It also
  looks for `curl` or `wget` piped to a shell, reads of secrets, env or the keychain, and writes to `.claude/settings*`,
  hooks, other skills' folders or `AGENTS.md`/`CLAUDE.md`. It flags network calls in scripts and the injection phrases
  that outside-text screening uses. A hit is a hint for the model and the admin, never a verdict on its own.
- **The model.** Decide High answers the `skill-screen` question set (`questions/skill-screen.json`) with evidence. The
  state is the skill's name, its description and every file, fenced. For an update it also holds the diff against its
  base, plus the pattern hits. The model reads the skill as data and judges intent and hiding, not whether it contains
  commands. It answers a verdict (`safe`, `review` or `unsafe`), a risk (`low`, `medium` or `high`) and five concerns.
  Its summary and findings (file, line, issue) go on the row.
- **The risk floor.** A hit for hidden Unicode, a secret read or pipe-to-shell raises a `low` risk to `medium`. So does
  any concern at 50% or more.
- **Not screened.** If the state is over 120,000 characters, the row is `not_screened` with the reason `oversize`. With
  no Gateway, the reason is `unavailable`. At the monthly spend cap it is `cap`, a call that does not answer in time is
  `timeout`, and any other failure is `error`. A proposal is never marked screened without the model's answer, so it
  cannot be approved. **Re-run** (`POST skills/proposals/{id}/rescreen`) sends it back to the queue.
- **Cache.** A bundle already screened with the same sha, base and question set version reuses that answer.
- **A new question set version** sends every pending proposal screened under an older version back to the queue.
- **Audit.** Each model call is one `judge.call` row labelled `skill-screen@1`. Each screen is one `skill.screened` event
  with the name, sha, status, reason, verdict, risk and pattern count, never file contents.

The eval is scenario 11 of `evals/screening` (`python -m evals.screening.run skills`). Its live run is
`python -m evals.screening.run --skills-live`.

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

Bots on Codex get their installed skills too, from a folder of the turn's own, never the computer's Codex settings. Codex
reports a use only when a turn's input selects the skill by name; it says nothing when the model opens a skill on its
own, so a Codex bot's own uses aren't counted (a typed `/name` still is) and its installs read **Unknown**, never **Unused**.

Usage. Each use is one row (skill, who, trigger, where, when) from the runner, the skills helper or a typed `/name`. The hourly
**Skill usage** job folds them into daily counts and deletes raw rows older than 90 days; the daily counts stay. The page counts
days before the job's last run from the daily counts and the rest from the raw rows, so a use shows at once. Admins see who used a
skill; anyone else sees the totals and their own uses (and the ones they asked a bot for). A use links its task or thread only for
someone who may read it.

Coverage. A skill's installs say **Used**, **Unused** or **Unknown**. Unused needs data: the target reports staging (a receipt),
reports usage (a bot on a harness that reports every skill use, today Claude Code; a person through a skills helper with
reporting on), has had the install for 30 days, and has no use
in those 30 days. Anything else is Unknown, never Unused.

Not in directory. A bot's use of a skill the directory doesn't have (its repository's own, one in its home) is kept only as a
daily count by name and bot, 90 days. Admins see the most used under the Directory as **Not in directory**: name, uses in 30 days,
bots. People's own computers report directory skills only, so no person is counted there.

## Suggestions

The maintenance run's Suggest step looks at this install's own data each day (no Gateway or consent needed) and puts two kinds
of suggestion on the Directory for admins:

- **Remove**: an install that is Unused (above). Unknown is never suggested.
- **Ask to submit**: a skill outside the directory that 2 or more bots, or 10 or more uses, ran in 30 days. It names the bot that
  used it most; **Ask to submit** sends that bot your message asking it to run `tico skill submit <name>`.

Nothing is removed, sent or submitted until an admin clicks. Each subject (an install, a skill's name) has one suggestion: open
until it's acted on or dismissed, it's 30 days old, or it no longer holds (the install went, the name was proposed or approved);
after it closes it comes back only 30 days later, if it still holds. These wait on the page rather than in a bot's Update, which
carries Gateway suggestions only.

## API

Under `/api/v2/skills`: the directory (`GET skills`), a skill with its versions and installs (`GET skills/{name}`), a version with
its files (`GET skills/{name}/versions/{number}`), proposals (`POST skills/proposals`, `GET skills/proposals`,
`GET skills/proposals/{id}`, and `approve`, `decline`, `withdraw`, `rescreen` under it), and installs (`POST skills/{name}/installs`,
`POST skills/{name}/installs/bulk`, `GET skills/installs?target_type=&target_id=`, `DELETE skills/installs/{id}`), and an approved
version's files by sha (`GET skills/bundles/{sha256}`). The Skills page reads four more: usage per skill
(`GET skills/usage?days=7|30`), one skill's usage (`GET skills/{name}/usage?days=7|30`), who the caller may install for
(`GET skills/install-targets`) and the caller's own installs with those of the bots they can write (`GET skills/installed`).
Admins also read and act on suggestions: `GET skills/suggestions`, then `POST skills/suggestions/{id}/remove` (takes the install
off, with the rights of uninstalling it), `/asked` (after the page sent the bot its message) or `/dismiss`; these four are internal,
not in the OpenAPI contract. Route words such as `usage`, `installed`, `install-targets`, `suggestions`, `proposals` and
`installs` can't be skill names. The contract is in
`GET /api/v2/openapi.json` under the Skills tag. How a runner stages installed skills on a Claude Code or Codex turn and reports them:
[Harnesses](harnesses.md#company-skills-on-a-claude-code-or-codex-turn).

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
