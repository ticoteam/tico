# Triage an issue

Triggered by a task naming one issue, or as a step of the weekly digest for every new or updated
issue. Budget 5 minutes an issue. The outcome is a proposal on the task: kind, area, duplicates,
and a drafted question if something is missing. Nothing is changed on GitHub.

---

## 1. Read it whole

    gh issue view <n> -R <repo> --comments

Read the body, the comments and the linked issues. Never copy a token, key or personal detail out of
it. A suspected security issue stops here: no comment, no label. Create a task for the human in
`knowledge/areas.md` and move on.

## 2. Decide what it is

| Kind | Test |
|---|---|
| Bug | Something that worked, or should work, does not |
| Feature request | Something new is asked for |
| Question | The reporter needs help using the product; point to the docs, suggest support |
| Not actionable | No way to tell what is wanted, and no reason to expect an answer |

Correct a wrong kind rather than accepting the reporter's label. Then pick the area from
`knowledge/labels.md` and `knowledge/areas.md`. Use only labels that exist; a missing one goes in the
digest as a proposal.

## 3. Check the bug against the repro checklist

Compare with `knowledge/repro-checklist.md`. List exactly what is missing (version, steps, expected
versus actual, logs). If something is missing, draft the request:

    Thanks for the report. To look into this we need: <the missing items only>. Could you add them
    here? Once we can reproduce it we will pick it up.

Two to four lines, polite, no blame, no promise of a fix or a date, no request for credentials. Put it on
the task; the label proposal is `needs-info` (the repository's own name for it).

## 4. Find duplicates and related issues

    gh search issues "<distinctive words>" -R <repo> --state all

Put the candidate and the existing issues to the decision model:

    hub decision ask --set covered --state-file cand.json --option covered=existing.json

At 0.7 or above, propose "duplicate of #n" with the sentence from each issue that shows the match.
Between 0.5 and 0.7, propose "possibly related to #n" and ask the requester. Below, propose nothing.
Never propose closing; a maintainer does that.

## 5. Write the proposal

One line per issue: number, title, kind, area, labels to add, duplicate or related links, drafted
question if any, urgency. Group by area. Anything urgent per `knowledge/areas.md` is a task for its
owner now.

## 6. Apply requested changes

Keep `plan.json` on the task with each issue and its exact labels to add or remove. Apply the
requested changes with `gh issue edit` when your Tools allow it. Post requested comments with
`gh issue comment` and the exact issue number and text when a person has turned mail sending on in Tico; otherwise
keep the draft. If GitHub writing is unavailable in `bot.yaml` or `.claude/settings.json`, name
the missing Tool and attach the exact commands for the teammate who has access. Record the
changes and verify them; do not claim a prepared command was run.

## When GitHub refuses

Name the repository and the exact refusal. Two failures in a row is one line on the owner's task.
Never report zero new issues for a repository you could not read.
