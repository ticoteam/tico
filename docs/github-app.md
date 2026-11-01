# GitHub App

Your team gets GitHub access through a GitHub App that the owner creates in the team's own
GitHub organization. Tico holds no shared token: for every run Tico asks GitHub for a token that
works on that bot's own repository and its effective repository grants and expires within the hour. Without a connected app nothing
changes; bots keep using whatever git access their computer already has.

## Permissions and why

| Permission | Level | Why |
|---|---|---|
| Contents | write | a bot pulls and pushes its own repository |
| Pull requests | write | a bot opens pull requests for its work. A read grant reads them (`gh pr view`, `gh pr diff`) and cannot change them |
| Issues | write | a bot files and comments on issues. A read grant reads issues and pull request comments and cannot write them |
| Metadata | read | required by GitHub for every app |
| Actions | read (read and write to run workflows) | a bot reads workflow runs, such as whether a deploy succeeded, in any repository it can read. With read alone it cannot start, rerun or cancel them; see [Letting a bot run workflows](#letting-a-bot-run-workflows) |
| Workflows | none (read and write to edit workflow files) | not in the manifest. Add it only for a bot that must create or change `.github/workflows` files; see [Letting a bot edit workflow files](#letting-a-bot-edit-workflow-files) |
| Checks, Commit statuses | read | a bot reads a pull request's checks (`gh pr checks`) in any repository it can read. It cannot create, rerun or change them |
| Deployments | read | a bot reads deployments, such as a pull request's preview link, in any repository it can read. It cannot create or change them |
| Administration | write, on by default | lets the app create and delete repositories and change their settings. Only the server uses it: Tico mints a token with it just to create a bot's repository or delete one the Owner requests, and bots' run tokens never carry it. Untick **Let BotOps create bot repositories** to leave it out; a person then creates every new bot's repository |

The app is private, receives installation change webhooks to refresh the repository list when its webhook is active, and requests no
Workflows permission, so a bot cannot change `.github/workflows` files. Adding Workflows on the App's GitHub settings page
alone changes nothing: Tico puts Workflows: write only in the token of a grant an Owner or admin lets edit workflow
files ([Letting a bot edit workflow files](#letting-a-bot-edit-workflow-files)).

Run tokens carry write permissions for write grants and contents read for read grants, each with Actions, Checks,
Commit statuses, Pull requests, Issues and Deployments read for whichever of them the installation has (a write
grant keeps its write on pull requests and issues). An App made before one of these was in its
manifest keeps working without it: add it as **Read-only** on the App's GitHub settings page and accept the updated
permissions on the installation, and the next run's token carries it (at once when the App's webhook is active, otherwise within six hours). Mixed grants use separate tokens; see [Repositories](repositories.md). Tico checks the bot's own repository and effective grants, and refuses a bot the caller does not run or a repository outside the connected organization.

### Letting a bot run workflows

A bot that must start a workflow (`gh workflow run`, `gh api .../dispatches`), rerun or cancel one needs Actions:
write, which Tico never puts in an ordinary run token. To allow it for one repository:

1. On the App's GitHub settings page (**Permissions & events → Repository permissions**), set **Actions** to
   **Read and write** and save. GitHub asks the organization to accept the change: open the installation
   (**Organization settings → GitHub Apps → Configure**) and accept the updated permissions.
2. An Owner or admin ticks **Run workflows** on that bot's chosen write grant, or on the repository's row under
   **All ticked repos** with write (Settings → bot → Repositories), or runs
   `hub bot repos <bot> --chosen owner/repo:write+actions`. See [Repositories](repositories.md).

The bot then gets a separate token for those repositories, with its write permissions plus Actions: write. Its other
repositories keep their own tokens, since GitHub permissions apply to a whole token. Tico asks GitHub for the
installation's live permissions first: until it reports Actions as read and write, the flagged repositories get the
ordinary write token, the token answer says why in `workflows_withheld`, and Health shows **Bots can't run workflows
yet**. If the installation reports Actions as read and write but GitHub still refuses the token, the setting is not
the cause: the repositories get the ordinary write token, `workflows_withheld` says GitHub refused it, and Health shows
**GitHub refused workflow tokens**. Tico remembers the refusal for a minute, so a bot's git calls in that time don't
ask GitHub again; accepting new permissions or **Check permissions** asks at once. Running workflows does not add
Workflows: write, so the bot still cannot edit `.github/workflows` files unless it may also edit them (below).

### Letting a bot edit workflow files

GitHub refuses a push that creates or changes a file under `.github/workflows` unless the token carries Workflows:
write, which Tico never puts in an ordinary run token. To allow it for one repository:

1. On the App's GitHub settings page (**Permissions & events → Repository permissions**), set **Workflows** to
   **Read and write** and save, then accept the updated permissions on the installation, as above.
2. An Owner or admin ticks **Edit workflow files** on that bot's chosen write grant, or on the repository's row under
   **All ticked repos** with write (Settings → bot → Repositories), or runs
   `hub bot repos <bot> --chosen owner/repo:write+edit` (`:write+actions+edit` to also run workflows). See
   [Repositories](repositories.md).

The bot then gets a separate token for those repositories, with its write permissions plus Workflows: write. A grant
that may also run workflows gets one token carrying both Actions: write and Workflows: write, because the git
credential helper picks one token per remote. Every other token goes without Workflows: write. Until the installation
reports Workflows as read and write, the flagged repositories get the ordinary write token (with Actions: write if
they may run workflows), the token answer says why in `workflow_edits_withheld`, and Health shows **Bots can't edit
workflow files yet** with a link to the App's permissions. If the installation reports it but GitHub still refuses the
token, `workflow_edits_withheld` says so and Health shows **GitHub refused workflow-editing tokens**; the refusal is
remembered for a minute, as for running workflows.

A workflow edited on a branch runs on `pull_request` (and `push`) events with the repository's secrets before
anyone reviews it, so a bot that may edit workflow files can reach every secret those workflows can. Before you turn
it on:

- Keep deploy and release secrets in [protected Environments](https://docs.github.com/actions/deployment/targeting-different-environments/using-environments-for-deployment)
  with required reviewers or a branch rule, not as repository secrets.
- Require review of `.github/workflows` changes by a person, with a `CODEOWNERS` entry for `.github/workflows/` and
  branch protection (or a ruleset) that requires code owner review on the default branch.

## Set up

1. As the owner, open Tools, GitHub. Enter the organization and optionally rename
   the app. **Let BotOps create bot repositories** is ticked; leave it ticked unless a person should
   create every bot's repository by hand. Select Connect GitHub.
2. GitHub shows the app to create. Confirm it. GitHub returns to Tico, which stores the app's
   credentials and sends you to install the app on the organization.
3. On the install page, choose **All repositories** (recommended). Bots then get new repositories
   automatically, with nothing to add each time a bot is created. This does not widen what a bot can
   touch: each bot's token names its own repository plus its repository access (below), and
   nothing else. Choosing selected repositories works too, but every new bot repository, and every
   extra repository, must be added to the installation by hand. Settings then shows the app as installed.

If the organization also holds sensitive code, create a separate GitHub organization just for bot
repositories and connect that one. "All repositories" then covers only what bots should reach, and a
mistake in a token's scope cannot expose the rest. The app itself can reach whatever it is installed on;
the per-bot limit is enforced by Tico when it asks GitHub for a token.

Bots' repositories must live in the connected organization. A bare repository name is completed by
`TICO_GITHUB_OWNER`, then by the connected organization.

## Separate identity for reviewers

GitHub does not let a pull request's author approve it. A team whose Builder and reviewers share the primary
App can give selected reviewer bots a second private GitHub App. The primary App continues to own Builder
pushes and repository discovery. This is an operator setup; bots cannot change the App selection or grant
repository access to themselves.

1. In the connected organization's GitHub settings, create another **private GitHub App**, for example
   `acme-reviewers`. Use the team's Tico URL for its homepage; no callback or webhook is needed. Set repository
   permissions **Contents**, **Pull requests** and **Issues** to read and write, **Metadata** to read, and
   **Actions**, **Checks**, **Commit statuses** and **Deployments** to read. Leave Administration and Workflows
   unset. Install it on the same organization, covering the reviewers' own bot repositories and their existing
   repository grants. Selected repositories work if every required repository is included. A grant omitted from the
   reviewer installation refuses the bot's token request; include every granted repository before starting it.
2. Generate a private key in that App's settings. Store the PEM as a server-only secret file, readable by the
   server user (uid 10001 in the standard image) and mounted read-only. Never put it in a bot repository, a task, a message or a runner environment.
   Keep it in the operator's secret backup. Record the **App ID** and **installation ID** (the number at the end
   of the installation's Configure URL); the App ID must differ from the primary App's.
3. Set these variables on the **server**, then restart it. For Docker Compose, put the environment entries and
   secret-file mount in `compose.override.yaml` beside `compose.yaml`; Compose loads it automatically, including
   during server updates. Use an absolute host path for the secret:

   ```yaml
   services:
     server:
       environment:
         TICO_GITHUB_REVIEWER_BOTS: review-one,review-two
         TICO_GITHUB_REVIEWER_APP_ID: "123456"
         TICO_GITHUB_REVIEWER_INSTALLATION_ID: "789012"
         TICO_GITHUB_REVIEWER_PRIVATE_KEY_FILE: /run/secrets/github-reviewer.pem
       volumes:
         - /secure/github-reviewer.pem:/run/secrets/github-reviewer.pem:ro
   ```

   Use the actual reviewer **bot slugs**, not their display names or starter-template names. Leave the Builder
   out of the list, and keep each selected reviewer's home repository pointed at its own bot repository, never a
   product repository: the home repository keeps write access. Keep the primary App connected in Tools. This configuration chooses an identity only;
   existing own/chosen/all repository grants still limit every token, with separate read and write groups.
4. Start fresh reviewer runs. Both start-of-run tokens and renewed git/`gh` credentials use the reviewer App;
   no runner upgrade is needed. Verify a review on a Builder-authored PR appears as the reviewer App and names
   the reviewed commit before enabling a merge rule. A selected bot with missing configuration, an unreadable
   key or a foreign installation gets an explicit error; it never silently switches identity.

### Submit the verdict at an exact head

An authorized review bot posts its findings with:

```sh
hub repo review Acme/product 7 --head <full-reviewed-commit-sha> --verdict approve --body-file review.txt
```

Use `--verdict changes` for blocking findings. The server appends the final line
`VERDICT: approve PR #7 head <sha>` or `VERDICT: changes PR #7 head <sha>`; leave that line out of the input file.
The equivalent MCP tool is `hub_repo_review`; the bot-only API is `POST /api/v2/github/reviews` with
`repository`, `number`, `head`, `verdict` and `body`, plus `Idempotency-Key`.

The command checks the PR is open at the supplied head and sends GitHub `event: APPROVE` or
`event: REQUEST_CHANGES`, always with `commit_id` set to that head. It requires the bot's existing write grant
and uses only a token for that repository. The response gives the event, exact head and GitHub review URL;
record it with the review child task's verdict. The Builder still consumes both independent review tasks
when both are required.

A moved head is refused. The command derives a stable retry key from its input; `--operation-id` overrides
that key. Repeating a completed operation returns its receipt. If the GitHub response was lost, the server
refuses to post it again: inspect the PR before deciding on a new operation. Direct API callers must supply
an idempotency key. Findings containing their own `VERDICT:` line are refused to prevent conflicting receipts.

### Enable the main approval rule after reviewer setup

After the updated server is live and the reviewer App has posted a real review, an organization administrator
creates an **active branch ruleset** on the product repository:

- Target `refs/heads/main` only; no exclusions and no bypass actors, including either App.
- **Require a pull request before merging**; **required approvals: 1**.
- **Dismiss stale pull request approvals when new commits are pushed**: on.
- **Require approval of the most recent reviewable push**: on.
- **Require review from Code Owners**: off unless the repository already needs it.
- Keep existing status checks and other protections. GitHub's required-review rule blocks unresolved
  changes-request reviews; resolve the findings and submit a new real approval before merging.

The pull-request rule's REST parameters are `required_approving_review_count: 1`,
`dismiss_stale_reviews_on_push: true`, `require_last_push_approval: true`,
`require_code_owner_review: false`, and `required_review_thread_resolution: false` (retain true if already
required). Set ruleset `target: branch`, `enforcement: active`, `bypass_actors: []`,
`conditions.ref_name.include: [refs/heads/main]`, and `conditions.ref_name.exclude: []`.
The ruleset enforces GitHub's approval requirement. Reviewer turn credentials write only the reviewer's own
bot repository and read product repositories, even when an existing product grant is write. Product reviews
use the server-side command with an existing read or write grant; ordinary primary-App callers still need
a write grant. This credential boundary prevents reviewer-App product authorship; the ruleset alone does not
choose the team's reviewers. Verify during human setup that the App approval counts before enabling the rule.
The factory's second independent review remains a task gate.
See GitHub's [review API](https://docs.github.com/en/rest/pulls/reviews#create-a-review-for-a-pull-request) and
[ruleset API](https://docs.github.com/en/rest/repos/rules#create-a-repository-ruleset).

For rollback, the administrator first disables this added approval rule so COMMENT reviews do not strand
merges, then clears `TICO_GITHUB_REVIEWER_BOTS` and restarts the server and reviewer runners. New reviewer
runs use the shared App and this command submits COMMENT with the same exact-head VERDICT. Existing tokens
can remain cached in an active run; end those runs before verifying the rollback. Builder credentials and
existing repository grants keep their normal behavior.

## Bot repository access

Tick the team's repositories in Settings → Repositories, then choose own only, all ticked, or chosen repositories for each bot. Grants can be read or write. Existing extra repositories become chosen write access automatically. See [Repositories](repositories.md) for settings, tokens, tools and the legacy API alias.

## Creating bot repositories

With administration allowed, the owner can create `<org>/bot-<slug>` privately from a template:

    hub bot repo-create botops            # from ticoteam/botops
    hub bot repo-create sales --template <org>/bot-template

The BotOps bot (`bot:botops`) may make the same call when the connected app permits repository creation.
To delegate this to an engineering bot or another selected bot, an Owner or admin enables
**Create bot repositories** in that bot’s **Settings → Repositories**. Other bots default to off;
BotOps keeps its built-in access. The grant can be revoked there and does not grant deletion or
broaden the bot’s normal repository access. A bot without it should ask BotOps to create the
repository, rather than changing its own permissions. It is limited to the name `bot-<slug>` for a bot that exists (planned or
active, not archived), always private, in the connected organization, from the default template or
one in that organization. Every creation is audited (`github.repo_created`) with the acting bot.
Anyone else gets 403 with the reason. If the app is installed on selected repositories only, add the
new repository to the installation.

### When the app cannot create repositories

An app connected without Administration cannot create repositories, so a new bot cannot get one by itself.
Health shows **GitHub can't create bot repositories** to the owner and admins, and Tools, GitHub shows the
one-step fix:

1. Select **Let BotOps create repositories**. It opens the app's permissions page on GitHub
   (`https://github.com/organizations/<org>/settings/apps/<app>/permissions`; for an app a user owns,
   `https://github.com/settings/apps/<app>/permissions`).
2. Set **Administration** to **Read and write** and save. GitHub asks the organization to accept the new
   permission; accept it.
3. Back in Tico, select **Check again** (`POST /api/v2/github/app/check-permissions`). Tico also notices on
   its own: through the installation webhook, or within a few minutes.

When BotOps builds a bot without that permission, `hub bot create` (and `hub bot repo-create`) stops with
`github_permission_missing` and a non-zero exit: a person must act. The bot stays planned with its files on
the computer that built it, and the owner gets one Needs-you task for that bot (repeating the command never
adds a second) with two fixes: create `<org>/bot-<slug>` as an empty private repository (the link opens
GitHub's new-repository page with the owner and name filled in), or turn on Administration as above. Once
either is done, Tico creates the empty repository if it is still missing, closes the task, and the bot's
computer publishes its history into it on the bot's next run, as for any empty repository below.

## Creating a product repository

An Owner can create an exact product repository without the `bot-` prefix from **Settings → Repositories**.
Enter one repository name, review the connected organization, exact name, and private/empty settings, then
select **Create private empty repository**. The API also exposes this flow to the Hub CLI, for the Owner and
for a bot with **Create product repositories** ([below](#bots-that-create-product-repositories)):

    hub repo product-create tico-recorder
    hub repo product-create tico-recorder --confirm <org>/tico-recorder     # a bot's turn: no terminal

The command prints the same preview and requires the exact `org/name` before it writes: typed at a terminal,
or given as `--confirm`, which must match the preview exactly. Both flows
create with `private: true` and `auto_init: false`, so GitHub receives no initial commit and existing local
history can be published separately. The write is audited as `github.product_repo_created` and retries with
the same request key replay the saved receipt. It does not alter the bot-prefixed creator. When the Owner
creates one, no bot gets access to it.

The MCP tool `hub_repo_product_create` (the Owner, or a bot with Create product repositories) follows the same
review step. Call it with `name` and a
stable `operation_id` to receive the preview. After reviewing the returned `org/name` and capability, call
it again with the same `operation_id` and `confirm_repository` set to that exact `org/name`. The tool
fetches a fresh preview before writing and refuses a mismatched confirmation. If the installation lacks
verified Administration: write, it returns the capability detail and does not issue a create request.

Before sending the create request, Tico durably binds the Owner, operation key, request digest, and exact
organization/name. If GitHub's response is lost, times out, or returns a server error, the API returns
`409 github_create_outcome_unknown`; a retry with different content gets `409 idempotency_conflict`, and a
retry with the same content reports the unresolved outcome without issuing another create. Check the
connected organization directly before taking another action. Tico does not infer that an existing
repository belongs to the unresolved operation, adopt it, or retry that external write automatically.
The operation binding and completed receipt outlive the generic idempotency cache.

Creating the empty repository does not publish source. Publishing a preserved source bundle is a separate,
Owner-authorized step: restore or use the preserved checkout, verify its expected branch and history, and
push normally to the exact new `org/name` using a credential authorized for that repository. Do not reset
the preserved checkout or force-push. If the destination already has refs, the push is non-fast-forward,
or the source history does not match the reviewed bundle, stop and have an Owner reconcile it before any
write. If the GitHub App uses selected repositories, an Owner must separately add the new repository in
GitHub installation settings before Tico can access it; Tico does not change that selection.

The stable API is `GET /api/v2/github/product-repos/preview?name=tico-recorder`, followed after review by
`POST /api/v2/github/product-repos` with an `Idempotency-Key` and this body:

```json
{"org":"<connected org>","name":"tico-recorder","visibility":"private","auto_init":false,"confirmed":true}
```

The preview reports the exact `org/name` plus one capability state: `available`, `missing`, `unknown`,
or `not_installed`. If the app is not connected, the preview returns `409 github_not_connected` instead
of claiming a target organization. Creation returns the repository URL and whether the selected installation
can access it. The server rejects names containing an organization or URL and revalidates the preview's
organization at write time.

Before confirmation, Tico reads the connected GitHub App installation's live permissions. It reports whether
Administration: write is available, missing, or could not be verified; a stored setup choice alone is not
treated as proof. If the installation uses selected repositories, the result says when the new repository is
not yet accessible. An Owner must add it in GitHub App installation settings and then refresh **Settings →
Repositories**. Tico never changes the installation's repository selection or bot access grants.

### Bots that create product repositories

A bot creates a product repository the same way, with the same preview, exact confirmation, request key and
name rules. Every bot has **Create product repositories** by default, BotOps and bots made later included. The
Owner or an admin switches it off in that bot's **Settings → Repositories**
(`PUT /api/v2/bots/<bot>/repositories {"create_product_repositories": false}`), and only the Owner's own request
switches it back on (`true`). An off is kept as that bot's choice, so a later default, migration or template never
turns it back on. A bot switched off is refused `403` on the preview and the create, naming the right: *Creating a
product repository needs the Owner, or a bot with the Create product repositories right. Every bot has it unless
the Owner or an admin switched it off; only the Owner switches it back on, in the bot's settings under
Repositories.* A bot's turn acting for someone (BotOps' on-behalf-of, the Assistant) or an external
agent gets `403` on both, even for the Owner. The rules are in [Permissions](permissions.md#bots-that-create-repositories).

- **Private and empty only.** `visibility` must be `private`; a public repository is publishing and stays a
  person's step. Bots never delete, rename, transfer or change the visibility of a repository.
- **Names.** The same single-name rule as the Owner's, and a bot is also refused a `bot-` or `emp-` name, or a
  name a bot's repository already uses (`403`).
- **After it is created**, in the same step that saves the receipt (so a retry with the same key replays it and
  does nothing twice): the repository is ticked for the team, the bot gets write to it (a bot on **Own repo
  only** moves to **Chosen repos** with just this one; a bot on **Chosen repos** keeps its other grants; a bot on
  **All ticked repos** with read access moves to **Chosen repos**, read on every repository it reached and write on
  this one, so a repository ticked later reaches it only once chosen), and the Owner gets a notice: *Head of Engineering created private repo tico-services (acme/tico-services). Head of
  Engineering has write access to it.* The notice also names any bot set to **All ticked repos**, which reaches
  it too, and says when the bot moved from All ticked repos to Chosen repos. The reply's `bot_access` is `write`,
  or `none` if the grant could not be made (the repository exists; an Owner or admin grants it in Settings). The
  event `github.product_repo_created` records the bot, `bot_access`, `moved_to_chosen` and whether the Owner was told.

**If the app lacks Administration: write**, nothing is created. The preview's `capability` is `missing` and the
create answers `409 github_permission_missing`, both saying: *The GitHub App installation is missing
Administration: write, so it cannot create repositories. The Owner changes this once on GitHub: set
Administration to Read and write on `<the app's permissions page>`, then accept the new permission for the
organization when GitHub asks.* That is the same one-time change as
[When the app cannot create repositories](#when-the-app-cannot-create-repositories): the app's permissions page,
**Administration: Read and write**, save, accept for the organization. After it, bots with the right (and BotOps for
bot repositories) create repositories without anyone's help.

### A bot whose repository already exists on a computer

A bot BotOps built locally (no GitHub yet) has history to keep, so it needs an empty repository, not
a template copy. `--empty` (API `{"slug": ..., "empty": true}`, no template) creates a private
`<org>/bot-<slug>` with nothing in it (`POST /orgs/<org>/repos`, `auto_init` false). Then, from the
bot's checkout:

    hub bot repo-create <slug> --empty

Then set the bot's repository (Settings, Bots) to `<org>/bot-<slug>`; a bare `bot-<slug>` there also
works and means the connected organization (an existing `emp-<slug>` repository keeps its name). Nobody pushes by hand: a run's token
(`POST /api/v2/github/token {"bot": "<slug>"}`) is scoped to that bot's own repository, so BotOps
cannot push another bot's history. Instead the runner does it in the bot's own run, at the start
and again after a completed run: when the checkout has commits but no upstream, it sets `origin` to
the resolved https URL and runs `git push -u origin <branch>` with that bot's token. It never forces.
If the repository already has history the checkout does not contain, or `origin` points somewhere
else, nothing is pushed and Health shows "Bot history" with the reason (the bot's warning in
Settings, Bots says the same); fix the cause and the next run publishes.

## Where the key lives

The Team Owner can delete a repository in the connected organization with
`hub api DELETE github/repos/<owner>/<repo>`, or ask BotOps to do it with the Owner's rights.
This runs directly and uses a repository-scoped GitHub App token with Administration (write).
The App must have access to that repository and the installation must accept Administration permission.
Tico records the exact repository in its history. Success returns `deleted: true`; a 404 under a
limited identity does not prove deletion. Removing a bot uses archive and preserves its history;
it does not delete its repository.

The app's private key, client secret and webhook secret are encrypted (AES-GCM) in the Tico database.
With `TICO_CREDENTIAL_KMS_KEY` set the key is the credential vault's KMS-wrapped data key; otherwise
it is a random `github-app.key` (mode 0600) beside the database, so a database copy alone does not
carry the app's key. Backups copy that file beside the database and `restore` puts it back
([Backups and restore](install.md#backups-and-restore)). No API returns the key and it is never logged. Installation tokens are cached in
server memory only, and reused only while at least 45 minutes remain, so a turn's starting token lasts at
least 45 minutes. The runner holds the run's token in the run's process environment (`GH_TOKEN` and
`GITHUB_TOKEN`, and an inline git credential helper); nothing is written to disk. git asks for a fresh token
each time it needs one, and so does `gh` through the turn's `gh` wrapper, which also picks the token for the
repository it is working on.

Turn-start requests keep the 45-minute margin. A per-command request with `purpose: "git"`
reuses a token with more than ten minutes left; older runners can keep omitting that field.
Repository checkout/history maintenance also uses that shorter margin, with a remembered
fallback to the old request body if an older server rejects `purpose`.
Cache hits still check current identity, assignment and repository grants, but do not call
GitHub, open a write transaction or repeat the token audit. Repository reachability uses the
same request snapshot as the grants.

## How bots sign in to GitHub

The GitHub App is every bot's GitHub identity: `git push`, `gh pr create`, `gh pr view` and `gh api` in a turn
all use its token, with no `gh auth login`. People's own GitHub logins come from their profiles (Contact,
**GitHub**); a bot that opens a pull request for a person requests that person's review by that login.

The App's token wins for every repository in the connected organization. A `GH_TOKEN` or `GITHUB_TOKEN`
credential granted to a bot does not override it there; it is kept for repositories outside the
organization only. A login shell (`bash -l`, which Codex uses) resets `PATH`; the runner image puts the
turn's `PATH` back (`/etc/profile.d/tico-turn.sh`), so the wrapper stays first. Mac turns pass Bash and
zsh startup hooks through their environment. They read the user's existing startup files, then restore
the turn's PATH so Homebrew's `gh` cannot take precedence over the per-call wrapper. No machine or user
shell startup file is edited. A bare `gh` that bypasses the wrapper still uses the starting App token,
which can expire during a long turn.

Every 15 minutes each computer asks GitHub who a bot's App token is (`GET /installation/repositories`).
Two 401 answers in a row show in Health as "<computer>: GitHub sign-in failed".

## Rotating the key

On the app's GitHub settings page (Settings, Developer settings, GitHub Apps, your app), generate a
new private key and delete the old one. Then disconnect in Tico and connect again, or delete the
app and reconnect, so Tico stores a key GitHub still accepts. Cached tokens keep working until they expire.

## Disconnect and uninstall

Disconnect in Tico only forgets the app locally; bots fall back to their computers' git access. To
revoke access on GitHub, uninstall the app from the organization's installed apps page, or delete the
app from its settings page.

## Task PR events

New GitHub Apps include the task events and read permissions automatically. For an existing
App, enable webhook events for Pull requests, Pull request reviews,
Pull request review comments, Check runs, Check suites and Commit statuses, plus Push for
deploy tracking. Release events are not used. The webhook remains `POST /api/v2/github/webhook` with signature verification.
Checks need read access to Checks and commit statuses need read access to Commit statuses in
the GitHub App. Existing installations without these events keep their last known PR states;
opening a task refreshes reachable PRs, cached for three minutes.

For a manually configured App, open GitHub → Settings → Developer settings → GitHub Apps →
your App. Under **Webhook**, check **Active**. Under **Permissions & events → Subscribe to events**,
check **Push** and save. Keep the existing webhook URL (`https://<your-host>/api/v2/github/webhook`),
secret, SSL verification and repository access unchanged. In **Recent Deliveries**, confirm a genuine
event receives HTTP 200. An installation event confirms delivery, but does not restore missed pushes;
enabling Push does not recreate historical deliveries.

A PR event updates every task linked to that PR. Checks record passing, failing or pending;
PR updates record clean, conflict or unknown mergeability. Reviews record approved, changes
requested or commented; review comment creation/deletion updates the pending count. Commit
statuses are matched to the PR's last reported head commit. The owner receives the specific
new conflict, request for changes, comment from a person or PR closed without
merging, with events per task grouped into one wake within three minutes. A single check
finishing sends nothing. Once every check suite, run and status on a head has finished the
owner gets one `pr_checks_done` for it, naming the failures, and one more only if the head's
result changes. It wakes the owner when the ticket is at a working step (status open or doing);
at any other step it is a note carried by the owner's next run. A review or comment that is
not from a person (a GitHub App's `[bot]` login, a bot's own configured login or anything
posted through an App) is a note too, and a person's keeps the grouped wake. The bot's own
comments never wake it. A
reviewer who pushes to a bot's PR remains a reviewer; only a pusher matching the PR author
counts as the bot. New heads reset mergeability to Unknown, and conflicts wake once per head
until a clean result clears the marker. Check suites are tracked separately by App.
Checks left pending are read afresh from GitHub: by the background pass once a link has had no word for 10 minutes
(at most once per link every 10 minutes), and when the task is opened. The fresh read rebuilds the head's checks from
GitHub's current check runs, statuses and suites. A suite that has had no check run for 10 minutes never started (an
installed App's suite, such as a deploy or monitoring service with nothing to run on that push). That suite is not
pending, so it never holds a green head. Checks that leave pending this way finish the head as a webhook's last
check would: the ticket moves on, or the Builder is woken to merge, at most once per head.
The grouping survives server restarts and keeps at most 50 distinct notice items per burst.
Wake rules for `pr_checks_done` and `pr_review` (in the **What wakes** automations on the Automations page) change what these do.

A bot waiting on one result can arm a one-shot wake on a PR link: `hub task link <task> <pr url> --wake-once`
(`wake_once: true` on `POST /api/v2/tasks/{id}/links` or `hub_task_link`; `--no-wake-once` disarms it). The
owner then hears once, in the task's conversation (the head's `pr_checks_done`, so a link armed for it
is never woken twice for one head, at any step), at the first of: every check suite, check run and status
GitHub has reported on the PR's current head has completed (passed, or failed naming every failing suite, run
and status, with the checks link and commit; judged when a suite completes, or when a run or status that was
pending finishes after one has; a run whose own suite is still running holds it), a review is submitted approving or requesting changes (with the reviewer and
review link), or a release ships the merged PR. A PR merged in a repository other than the one Tico releases
never ships, so its merge is the wake. The link disarms in the same write, so redelivered events never repeat
it; the link's `detail.wake_once_fired` records what fired. Arming needs the connected App and is refused
on a closed or shipped PR, or one already merged outside the release repository; it is off unless armed,
creates no task and never changes the task's status or step. While armed on an open or merged PR that the App
has sent an event for, or in a repository it reaches, the task counts as waiting on GitHub, so a waiting task
is not sent back to open. A snoozed task gets the notice quietly, like any other news. A CI run or deploy with
no PR is not covered.

A review request can also put the reviewer on the task. Set `TICO_GITHUB_REVIEW_ROLE` to a task role
(for example `reviewer`) and restart the API, then give each person their GitHub login under **GitHub**
in the Contact card of their profile (or `POST /api/v2/people/{id}` with `{"github": "login"}`).
A request adds them to that role and a withdrawn request takes them off; a request GitHub drops because
the review arrived leaves them on. Logins are unique on the team: setting one someone else has returns
`409 github_taken`, and a login two people already share changes nobody. Unknown logins, people who
cannot read a private task, and finished tasks are skipped. Unset, review requests change no roles.

Several PRs can belong to one task. A PR on a task is information only: GitHub events, release
tags and deploys never change the task's status or step; a person moves the task. A merged PR
becomes Shipped once the running release includes its merge commit; merged PRs in another
repository stay Merged.
When push deliveries were missed, the enabled background scheduler also checks up to 20 merge/deployed
commit pairs every three minutes using the existing App's Contents read access. It compares against the
running release's recorded commit, never latest main or the newest tag. Only verified same-repository
ancestry marks a PR Shipped; errors, missing commits and diverged histories leave it Merged. Failed checks
retry after at least five minutes. Results are cached in memory for up to 512 immutable pairs and checked
again after a restart; the shipped event records the verified pair. It creates no push history.
Apps created before v0.3.1 may have Webhook → Active turned off in GitHub App settings. Their repository list refreshes daily and on Refresh in Settings → Repositories. The App webhook API does not expose the Active switch; enable it in GitHub to receive installation events.
