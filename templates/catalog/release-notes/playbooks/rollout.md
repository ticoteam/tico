# Release rollout

Triggered by the owner's approval of a named release on its release task. Budget: as long as the workflows take,
usually under an hour. The outcome is the release live on every install in `knowledge/rollout.json`, or a stop at the
first failure, with the before and after versions on the task. Speed matters more than ceremony: do not wait for
anyone between steps.

## 0. The one gate: the owner's approval

Start only when one of these is on the release task, for this exact version:

- an approval you requested (`hub approval request --kind publish --task <id>` with the version and commit) that
  `hub approval show <id>` says the owner approved; or
- the owner's own message on the task that approves it and names the version ("ship 0.3.23", "approved v0.3.23").

Approval of a different version, or a teammate's or bot's message, is not approval. Do not ask again, do not ask
anyone else, and add no other check: once it is there, run every step below in this run.

The commit is the one named in the readiness report or the approval; without one, the head of the default branch at
the moment of approval (`gh api repos/<repo>/commits/<branch> --jq .sha`), recorded on the task.

## 1. Run it

    python3 software/rollout.py all --version X.Y.Z --sha <commit>

It does, in order, stopping at the first failure:

1. **Tag.** Creates `vX.Y.Z` at the approved commit through the GitHub API with this bot's write grant (a tag made with
   the GitHub App's token starts the release workflows). A tag already at that commit is fine; one at another commit stops.
2. **Wait for the GitHub release.** Installs update from the release's bundle, so it waits for that and for nothing
   else. A failed Release or Docker workflow for the tag stops the rollout.
3. **Canary.** Updates each install marked `"canary": true` (the team's own install) through its update key: check for
   updates, start the update, follow it until the updater reports `healthy` and `/healthz` reports the new release.
   Then it waits up to ten minutes for that install's computers to follow and reports how they stand.
4. **The rest together.** Updates every other install at once, the same way.

It prints a JSON report: per install `before`, `after`, `outcome` (`healthy`, `already`, `rolled_back`, `failed`,
`refused`, `timeout`) and computers by state. While it runs, keep `hub bot status set` on the step it is on.

If the run is cut short, run the remaining step on its own (`tag`, `wait`, `update [--only NAME]`); each one is safe
to repeat. `status` prints what every install runs now.

## 2. When something fails

Stop and report; do not retry a failed update or try another version. The updater has already rolled that install
back on its own. Comment on the release task with the step, the install, the updater's message and the before and
after versions of every install, and `hub task ask` the owner what to do. A canary failure means no other install
was touched.

## 3. Report

On the release task, one comment: the version and commit, the release link, and per install before → after with its
outcome and computers. Update `knowledge/versioning.md` with the release and date, commit, and close the task.

## Setup (once, by the owner)

- `knowledge/rollout.json`: the repository and each install (copy `knowledge/rollout.example.json`). Mark the team's
  own install `"canary": true`.
- On each install, its owner runs `hub service-key create --label "<this bot>" --scope update` and stores the key in
  this team's Tools > Credentials, granted to this bot, with the variable named in `key_env`.
- Write access for this bot on the repository: `hub bot repos <slug> --chosen <owner>/<repo>:write` (keep the grants it
  already has in the same command).
