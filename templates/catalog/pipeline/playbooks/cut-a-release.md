# Cut a release for the day's tickets

Triggered by the daily task Tico sends ("Release: <n> tickets at Pending Release"), or by a person running `hub release now`,
or by a task asking for a named release. Budget 60 minutes. The outcome is one release ready for the owner: a readiness
checklist with a go or no-go, the notes, a suggested version, and an approval request. Nothing is tagged until the owner approves
that exact version. Once it is approved and rolled out, Tico takes the post-release hand-offs (QA and docs) itself.

---

## 1. Read the task

    hub task show <id>

List the tickets it names. Leave out any ticket you cannot open, and never ask for its name. The notes cover the tickets you can read.

## 2. Pick the candidate commit

    gh run list -R <repo> --branch main --limit 10 --json databaseId,headSha,conclusion,createdAt,workflowName

The candidate is the head of the default branch the task names, or the head of the default branch now if it names none. Name the
commit on the task. If the checks on it are not green, the readiness check says so, and the release is a no-go until they are.

## 3. Readiness

Follow `playbooks/release-readiness.md` for that commit. It writes `reports/releases/<version>-readiness.md` and ends in a go or
no-go with its reason. A no-go stops here: say what blocks it and who clears each item, then wait for the owner.

## 4. Notes and version

Follow `playbooks/release-notes-draft.md` over the range from the last release that reached users to the candidate commit.
Use `knowledge/versioning.md` for the numbering and suggest the version with its reason, from `skills/find-hidden-breaking-changes/SKILL.md`
for any change that might be breaking. The notes must cover every ticket you could read; a ticket with no merged pull request in the
range is listed under "Could not classify" with the question.

## 5. Ask for the approval

    hub approval request --kind publish --task <id>

Name the version and the commit in the request, as `playbooks/rollout.md` step 0 describes. The approval names one version at one commit. Put the readiness recommendation and the notes' SHA-256 in the request's note. Tell
the owner in one line what is waiting: "Release 1.4.2 is ready at commit abc1234. Approve it on the task to roll it out."

## 6. Roll out, only after approval

Once `hub approval show <id>` says the owner approved that exact version and commit, follow `playbooks/rollout.md` from its step 1
in one run. Any other version or commit asks again. A failed step stops the rollout and goes to the owner as a question.

## 7. Finish

Update `knowledge/versioning.md` with the release, its date and commit, and record the release in `state.md`. The task gets one
comment: the version and commit, the release link, the notes' path, the readiness outcome, and per install before and after with
its outcome. Then `hub task done <id> -m "..." --version N`, with the version in the first line.
