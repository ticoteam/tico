---
name: ci-security
description: Harden GitHub Actions workflows. Pin every action to a commit, keep untrusted values out of shell commands, give each job only the permissions it uses, and keep secrets out of logs. Then triage what is left. Read when asked to secure, audit or review CI workflows, or before adding a workflow that publishes or touches cloud credentials.
---

# Safer CI workflows

The goal is workflows where a compromised dependency, a crafted pull request title or a leaked log line can do as
little as possible. Fix real exposure; a scanner's score is a guide, not the goal.

This file lives in Tico at `$HUB_DIR/skills/ci-security/SKILL.md`. In a person's own Claude Code it comes from the
`tico` plugin.

## 1. Find the exposure

Run these from the repository root. Each hit is a candidate, not a verdict.

```bash
# Actions not pinned to a full commit (a tag or branch can be moved under you)
grep -nE 'uses: [^.][^@]*@' .github/workflows/*.yml | grep -vE '@[0-9a-f]{40}'
# Expressions that land inside shell (inputs, matrix, event fields)
grep -nE '\$\{\{ *(inputs|matrix|github\.event|github\.head_ref)' .github/workflows/*.yml
# Cloud sign-in tokens and write scopes granted to a whole workflow
grep -nE '^\s{0,2}(permissions:|  id-token: write|  contents: write)' .github/workflows/*.yml
# Triggers that run fork code with secrets
grep -nE 'pull_request_target|workflow_run' .github/workflows/*.yml
```

For the expressions grep, keep only hits inside a `run:` block. In `with:` and `env:` they are safe.

## 2. Fix

**Pin every action to a full commit, with the version as a comment:**
- Write it as `uses: owner/action@<40-hex sha> # v4.6.2`.
- Find the commit behind a tag with `git ls-remote https://github.com/OWNER/ACTION 'refs/tags/v4^{}' 'refs/tags/v4'`.
  The `^{}` line wins, because an annotated tag's own id is not a commit: GitHub answers "No commit found" (422).
- Stay on the major version the workflow already uses. Copying a pin from another workflow can silently jump v4 to v7.
- Some actions read their setting from the ref, such as `dtolnay/rust-toolchain@stable`. Once pinned, they need it
  as an input (`toolchain: stable`).
- Local actions (`./...`) and Docker digests are already fixed.

**Keep untrusted values out of shell.** Pass them through `env:` and quote the variable:

```yaml
env:
  COMPANY: ${{ inputs.company }}
run: ./build --company "$COMPANY"
```

A matrix value is input too, when the matrix comes from a caller or a file. For a reusable workflow's input, set
the `env:` once at the workflow level.

**Give each job only the permissions it uses:**
- At the workflow level, use `permissions: contents: read` (or `{}`). Raise it on the one job that needs more.
- `id-token: write` (OIDC) belongs only on the job that assumes the cloud role. A reusable workflow gets only what its
  caller grants: when the callee uses no OIDC, the caller passes none.
- Set `persist-credentials: false` on every checkout that does not push.
- Give `contents: write` only to the job that pushes, and say what it pushes in a comment.

**Keep secrets out of logs:**
- Never echo a secret, or a file built from one.
- Send signing and keychain tools' output to `/dev/null`, and print your own one-line error instead. Their output can
  name certificate subjects.
- Mask derived values with `::add-mask::` before anything prints them.

**Treat these triggers with care.** `pull_request_target` and `workflow_run` run with secrets on events a fork can
cause. Never check out or run the pull request's code in them; read only metadata.

## 3. Check

- Run `actionlint` (pinned) on every workflow you touched.
- Run the scans in step 1 again: every remaining hit needs a reason.
- Changing a callee's permissions can break its caller. Read every `uses: ./.github/workflows/...` caller.
- After merge, watch the next real run of each touched workflow, including tag-triggered and dispatch-only ones.

## 4. Triage what is left

Some findings are not worth changing. Write down why, rather than leaving them silent. Common ones:
- `npm ci` in a job with write access installs third-party code with that access. A lockfile pins it. Moving the
  install to a job without write access is the full fix; decide per workflow.
- A scanner flags `workflow_run` even when the workflow reads only the triggering run's metadata. That is a false alarm.
- Pinning first-party actions you also maintain is optional. Moving targets are still the riskier default.

In the pull request, list each change, each finding left and why, and the scan or score before and after.
