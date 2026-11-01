# Release practice

Standing reference for release notes, version numbers, release readiness and rollout. The team's own versioning
and voice live in `versioning.md` and `voice.md`; this file holds general practice and the sources behind it. Where
the two disagree, the team's written rule wins, and the disagreement goes in `decisions.md`.

## What a changelog is for

- A changelog is written for people who use the product or depend on it, not a log of commits. One entry per change a
  user can notice, grouped by kind, newest release first, with the release date (Keep a Changelog 1.1.0).
- The six kinds are Added, Changed, Deprecated, Removed, Fixed and Security. Leave out an empty kind. Deprecations go in
  before the removal, not in the same release (Keep a Changelog 1.1.0).
- Commit messages and pull request titles are inputs, not the note. Conventional Commits 1.0.0 makes a commit's type and
  its breaking-change footer machine-readable, but a commit title can describe the code, not the user's result, and
  "fix" may hide a behaviour change. Read the description and the diff before you classify.
- Generated release notes from GitHub group merged pull requests by label, with exclusions. Use them as a first list, then
  edit: labels are applied by people and are often wrong or missing (GitHub Docs, automatically generated release notes).

## Version numbers

- Semantic Versioning 2.0.0: a MAJOR bump for an incompatible change to the public interface, MINOR for a compatible
  feature, PATCH for a compatible fix. Before 1.0.0 the public interface is not yet declared, so anything may change in a
  MINOR release. That is not permission to break users quietly: list the break as breaking.
- A MAJOR bump resets MINOR and PATCH. A pre-release label never ranks above its release.
- A date-based name is the right choice for a product that does not promise an interface. Say which scheme is used in
  `versioning.md`, and use only that one.

| What changed | Suggested bump | The line to write |
|---|---|---|
| A removed, renamed or required option, a changed default, a changed response, a required migration | major (or minor before 1.0, still listed as breaking) | "Breaking:" with what the user must do |
| A new feature that existing users can ignore | minor | "Added:" |
| Only fixes and internal changes that users would not notice | patch | "Fixed:" or left out |

## Release readiness

- Check the release commit, not the branch. If the default branch moves after the check, the check is for the old commit.
  Re-check the new commit or say which commit was checked.
- A check that was skipped, unavailable or not run is not green. Write it as "not checked" with the reason.
- A check that failed and then passed on a rerun is a failure to explain, not a pass. Fix, re-check, and say what changed.
- Name the rollback before the release, not after it (see below).
- Changes that must be announced before they ship (a breaking change, a security fix) have their wording and timing
  recorded with the approval.

## Rolling back

- A rollback restores the previous code. It is safe only if the previous code can read the data the new code wrote.
- Expand and contract: add the new column or format first and keep both readers working; move the readers; remove the old
  form in a later release. Rollback is safe until the removal, and the removal is a release of its own.
- Destructive data changes (a dropped column, a rewritten value) need a restore point or a reverse step, tested before the
  release.
- A feature flag turns behaviour off. It does not undo a schema change.
- A regression that already existed before the release is usually fixed forward with a patch. A rollback is a release too
  and needs its own approval.

## Staged rollout

- A canary is a small, time-limited rollout to a few installs, compared with the rest. Predefine what "bad" means (an error
  rate, a failed job count, a failed sign-in rate) before it starts, and keep the list short. On a bad signal, pause,
  then roll back or escalate (Google SRE Workbook, "Canarying Releases").
- Name the installs by their exact names. A filter that matches nothing must fail the step; otherwise a canary that did not
  run looks like a canary that passed.
- A release can publish while an optional job fails. Check each job and each asset before you say "released".

## Measuring release health

- DORA's software delivery metrics: deployment frequency, change lead time (commit to running in production), change failure
  rate, and failed-deployment recovery time, which replaced time to restore in 2023. A fifth metric, deployment rework rate,
  was added in 2024 (DORA, metrics history). Use them as trends for the team, not as targets for one release.

## Sources

- Keep a Changelog 1.1.0, keepachangelog.com/en/1.1.0 (read 2026-11-01). Kinds, ordering, deprecation and release date rules.
- Semantic Versioning, semver.org (the 2.0.0 rules: MAJOR, MINOR, PATCH, the 0.y.z rule, and pre-release precedence). Read
  as a snippet of the 2.0.0 release candidate page, 2026-11-01; the rules are stable from 2.0.0 onward.
- Conventional Commits 1.0.0, conventionalcommits.org (type and breaking-change footer). Read as a search snippet only,
  2026-11-01.
- GitHub Docs, "Automatically generated release notes" (current, undated). Read as a snippet, 2026-11-01.
- DORA, "DORA Metrics" history page, dora.dev/guides/dora-metrics/history (page updated January 2026; the metric changes
  are dated 2023 and 2024 on the page). Read 2026-11-01.
- Google SRE Workbook, "Canarying Releases", sre.google/workbook/canarying-releases (read 2026-11-01). The percentages and
  durations on that page are examples, not targets.
