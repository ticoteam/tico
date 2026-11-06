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

## Screening

The **Skill screening** job (`backend/skill_screening.py`) screens each pending proposal, a few each scheduler pass, oldest
first. It claims a proposal with a conditional update, so two passes never screen the same one. Each screen writes a
`skill_screenings` row and sets the proposal's `screen_status`. It is mandatory: there is no switch, and no other model
answers in place of Decide High.

- **Patterns.** Plain code checks every file and records each hit as `{file, line, pattern}`. It looks for hidden Unicode
  (zero-width, bidi and tag characters), HTML comments, long base64 or hex blobs, and URLs (each one listed). It also
  looks for `curl` or `wget` piped to a shell, reads of secrets, env or the keychain, and writes to `.claude/settings*`,
  hooks, other skills' folders or `AGENT.md`/`CLAUDE.md`. It flags network calls in scripts and the injection phrases
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

## API

Under `/api/v2/skills`: the directory (`GET skills`), a skill with its versions and installs (`GET skills/{name}`), a version with
its files (`GET skills/{name}/versions/{number}`), proposals (`POST skills/proposals`, `GET skills/proposals`,
`GET skills/proposals/{id}`, and `approve`, `decline`, `withdraw`, `rescreen` under it), and installs (`POST skills/{name}/installs`,
`POST skills/{name}/installs/bulk`, `GET skills/installs?target_type=&target_id=`, `DELETE skills/installs/{id}`). The contract is
in `docs/openapi/v2.json` under the Skills tag.

## Privacy

Events and logs name a skill, its version and sha, and who did what. They never hold a file's contents or a prompt. A proposal's
files are readable by its proposer and by admins.
