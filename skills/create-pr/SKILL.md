---
name: create-pr
description: Open a draft pull request for the current branch against the repo's base branch, with a title tag and body sections, then stop.
---

# Create a pull request

Shared procedure for a team's dev bots and for humans' own Claude Code sessions, where it is `/tico:create-pr`. The output is one DRAFT PR and its URL. A human reviews and merges. You do not.

This file lives in Tico, at `$HUB_DIR/skills/create-pr/SKILL.md`, so every bot has it in every run. A `reads:` sibling is on disk only in a chat run; `$HUB_DIR` is set in a routine run too. Tico's owner owns what it says: change it here, nowhere else, and no bot needs another bot's repo to open a pull request.

Humans' own sessions (threads) get this same file from the `tico` plugin (the README, "Skills in your own Claude Code"). There `$HUB_DIR` is not set: Tico is two folders up from this skill.

## Per-repo facts

Each team keeps one row per repository it opens pull requests against. Replace the example rows; keep the columns.

| Repo | Base branch | Title tag | Cheap checks (scoped to the files you changed) |
|---|---|---|---|
| acme/api (Rails) | `origin/develop` | `(B)` | `bundle exec rubocop <changed .rb files>` and `bundle exec rspec spec/path/to/file_spec.rb` |
| acme/web (Angular) | `origin/develop` | `(F)` | `npm run lint:check` and `npm test -- <path/to/file.spec.ts>` |
| acme/site (React + Vite) | `origin/main` | `(F) (site)` | `npx tsc -b` and `npx vitest run src/path/to/file.test.ts` |

Notes:
- Write down anything a bot would otherwise get wrong: which branch is the default and which is production, which lint command edits files, which test command runs the full suite.
- Cheap checks never include a full build, an e2e suite or a dev server.

## Steps

1. Fetch first: `git fetch --all --prune --tags`. Local `origin/*` refs in these checkouts are often days stale. Never say what is or is not deployed, merged, or on a branch until this has run.
2. Confirm you are on a work branch named `feature/<slug>`, `fix/<slug>`, or `hotfix/<slug>`, created with `git checkout -b <name> --no-track origin/<base>`. Run `git rev-parse --abbrev-ref @{upstream}`; if it prints the base or production branch, run `git branch --unset-upstream`. If you are on the base or production branch itself, create the work branch now and move your changes to it.
3. Review the diff: `git diff origin/<base>...HEAD`. Remove any comment that explains why the change was made or what bug it fixes. That reasoning goes in the PR body, not the source. A short comment that says what a function does, in the file's existing style, is fine.
4. Run the repo's cheap checks from the table, only on the files you changed. Do not run `npm run build`, `ionic build`, the full test suite, e2e, or anything that starts a dev server. Copy each command and its result for the body. If a check cannot run, say so in the body; do not skip it silently.
5. Commit with a conventional-commit message (`feat:`, `fix:`, `refactor:`, `test:`, `chore:`). The commit says what changed; the PR body says why.
6. Push the branch: `git push -u origin <branch>`.
7. Write the body to a file (see "PR body") and open a draft PR. `--base` takes the bare name, the base or production branch:
   ```bash
   gh pr create -R acme/<repo> --draft --base <base> --head <branch> \
     --title "<title>" --body-file /tmp/pr-body.md
   ```
8. Stop. Report the PR URL.

## Title

`<tag> <plain summary>`.

- Tag from the table: for example `(B)` backend or `(F)` front end.
- The summary is one plain sentence, capitalized, no trailing period, paraphrased rather than the commit message. If the change is about one page or screen, lead with it: `Create account page - stack email and phone on mobile`.

Examples: `(B) Record inquiry response time from the first reply` and `(F) Guard undefined message text in messages search`.

## PR body

Four parts, in this order, with these exact headings:

```
## What
<one to three sentences: what changed and where (files, endpoints, pages)>

## Why
<the problem and the evidence:
Sentry link, screenshot, customer message, or query result>

## How it was checked
<each command exactly as typed, and its result, one per line. Example:
`npm run lint:check` passed with 0 errors.
`npm test -- src/shared/services/foo.service.spec.ts` 12 passed, 0 failed.
If a check could not run, say which one and why.>

Not verified in production.
```

Keep the last line exactly as written. Nobody has watched this change in production and the reviewer needs to know that.

If the repo has a pull request template, keep its sections and add ours under "What". Screenshots go under "How it was checked" when the change is visual.

## Never

- Never merge. Not into the base branch, not with `gh pr merge`, not in the GitHub UI. A human merges. "Same rules as last time" never carries a merge instruction over.
- Never push to the base or production branch, directly or by merging one into the other.
- Never force push.
- Never open a non-draft PR and never mark a PR ready for review. `--draft` is not optional.
- Never use the repo's `/push` or `/merge` skills. `/push` deletes worktrees.
- Never delete a worktree or a branch you did not create in this task.
- Never claim something is or is not deployed without fetching first. For "is it in production", prefer `git tag --contains <sha>` over branch heads.
- Never add comments to code that explain why a change was made.
- Never run full builds, e2e suites, or dev servers as part of this procedure.
- Never let a work branch track the base or production branch.
- Never use interactive git (`-i`).

## After the PR is open: ask for a review
Every PR this skill opens gets a second pair of eyes. Right after `gh pr create`, file one task on
the reviewer for the repo and stop:

```
tico task create --owner <reviewer> --title "Review: <PR title>" \
  --body "<PR URL>. Done when: <the acceptance line from the task or issue>."
```

`<reviewer>` is the reviewer bot the team assigned to that repo (for example `backend-reviewer` or
`frontend-reviewer`). The reviewer fixes mechanical
things itself on your branch as a "Review fixes" commit and sends back only design-level changes,
each with a one-line reason; address those, push, and say so on the review task with `tico task comment <id> "..."`.
Never mark the PR ready yourself; a human merges.

In a thread there is no `tico`: file the same task, with the same owner, title and body, through Bot
Desk's `create_task` tool, unless the human has said they will get it reviewed themselves.

When running as a branch (`shared_from`), use your branch's bot slug for tasks and status. The original repository holds shared lessons; keep private chats and personal preferences out of it.
