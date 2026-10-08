# GitHub App

Your team gets GitHub access through a GitHub App that the owner creates in the team's own
GitHub organization. Tico holds no shared token: for every run Tico asks GitHub for a token that
works on that bot's own repository and its effective repository grants and expires within the hour. Without a connected app nothing
changes; bots keep using whatever git access their computer already has.

## Permissions and why

| Permission | Level | Why |
|---|---|---|
| Contents | write | a bot pulls and pushes its own repository |
| Pull requests | write | a bot opens pull requests for its work |
| Issues | write | a bot files and comments on issues |
| Metadata | read | required by GitHub for every app |
| Administration | write, optional | create bot repositories and delete repositories the Owner requests; omit it by leaving the box unchecked |

The app is private, receives installation change webhooks to refresh the repository list when its webhook is active, and requests no
workflow permission, so a bot cannot change `.github/workflows` files. Add Workflows: write on the
app's GitHub settings page if a bot's repository needs that.

Run tokens carry write permissions for write grants and contents read for read grants. Mixed grants use separate tokens; see [Repositories](repositories.md). Tico checks the bot's own repository and effective grants, and refuses a bot the caller does not run or a repository outside the connected organization.

## Set up

1. As the owner, open Tools, GitHub. Enter the organization, optionally rename
   the app, and choose whether Tico may create bot repositories. Select Connect GitHub.
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
Anyone else gets 403 with the reason. Without the administration permission the command says so and
how to create the repository by hand. If the app is installed on selected repositories only, add the
new repository to the installation.

## Creating a product repository

An Owner can create an exact product repository without the `bot-` prefix from **Settings → Repositories**.
Enter one repository name, review the connected organization, exact name, and private/empty settings, then
select **Create private empty repository**. The API also exposes this flow to the Owner's Hub CLI:

    hub repo product-create tico-recorder

The command prints the same preview and requires typing the exact `org/name` before it writes. Both flows
create with `private: true` and `auto_init: false`, so GitHub receives no initial commit and existing local
history can be published separately. The write is audited as `github.product_repo_created` and retries with
the same request key replay the saved receipt. This path is Owner-only; it does not alter the bot-prefixed
creator or grant repository access to any bot.

The Owner-only MCP tool `hub_repo_product_create` follows the same review step. Call it with `name` and a
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
carry the app's key. No API returns the key and it is never logged. Installation tokens are cached in
server memory only, and reused only while at least 45 minutes remain, so a turn's starting token lasts at
least 45 minutes. The runner holds the run's token in the run's process environment (`GH_TOKEN` and
`GITHUB_TOKEN`, and an inline git credential helper); nothing is written to disk. git asks for a fresh token
each time it needs one, and so does `gh` through the turn's `gh` wrapper, which also picks the token for the
repository it is working on.

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
failed check, new conflict, request for changes, comment from someone else or PR closed without
merging, with events per task grouped into one wake within three minutes. Passing and pending
checks and the bot's own comments never wake it. Requests for changes always wake it. A
reviewer who pushes to a bot's PR remains a reviewer; only a pusher matching the PR author
counts as the bot. New heads reset mergeability to Unknown, and conflicts wake once per head
until a clean result clears the marker. Check suites are tracked separately by App.
The grouping survives server restarts and keeps at most 50 distinct notice items per burst.

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
