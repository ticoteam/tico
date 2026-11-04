# Weekday review queue

Schedule: weekdays at 09:00 team time (routine `weekday-review-queue`), after setup. Also run by hand on request. Budget 45 minutes.

Inputs: `knowledge/standards.md`, `knowledge/patterns.md`, `knowledge/review-practice.md`, yesterday's queue in `reports/`,
and the open pull requests read with `gh`.

Outcome: one page in `reports/`: the open pull requests ordered by what needs a reviewer, with a draft review for each of
the top ten. Nothing is posted and nothing on GitHub changes.

Done when: every open pull request in scope is listed or counted, each of the top ten has a draft at a head commit you read,
the queue is published, and the task says which pull requests you could not read.

---

## 1. Read where things stand

    hub task show <id>

Then `knowledge/standards.md`, `knowledge/patterns.md` and yesterday's queue in `reports/`, so you skip what
has not changed since you last read it.

## 2. List what is open

    gh pr list -R <repo> --state open --json number,title,author,createdAt,additions,deletions,reviewDecision,isDraft,statusCheckRollup

Leave out drafts and pull requests that are not against a branch in scope. For each, record the age in working
days, changed lines, files touched (`gh pr view <n> -R <repo> --json files`), review state and whether checks pass.

## 3. Order the queue

1. Pull requests in a risky path from `knowledge/standards.md`, with the human who must review them. For each, list the
   other open pull requests that touch the same files: two changes to one file are reviewed together.
2. Pull requests waiting longest with no review.
3. Small, green pull requests ready to be merged after one look.
4. Pull requests over the team's size line, flagged "too large to review well".

Put at most ten at the top; the rest are one line each.

## 4. Draft a review for each of the top ten

Follow `playbooks/review-a-pull-request.md`. Attach each draft to the task, one per pull request. Skip a
pull request you already reviewed at the same head commit, and say so. When the head has moved since your last draft,
read the new commits and say which commit the draft now covers.

## 5. Write the queue and hand it over

`reports/YYYY-MM-DD-review-queue.md` in the shape of `knowledge/examples/review-queue.md`: the headline (open, blocking,
too large), the blocking findings, then the queue. Then `hub file publish reports/YYYY-MM-DD-review-queue.md`. A pull request
waiting more than three days goes to its reviewer as `hub task create --owner <person> --title "Review waiting: <pull request title>" --link <pull request url>`.

## 6. Learn

Add a mistake to `knowledge/patterns.md` when it appeared in a second pull request, with both numbers and dates.

## 7. Finish

Commit, then `hub task update <id> --status done --note`: pull requests read, drafts made, how many block a
merge, and which you could not read. Always finish it.

## When a source fails

If `gh` cannot read a repository or a diff is truncated, name the pull request and what was not read, review
what you can, and never call a pull request fine on a partial read.
