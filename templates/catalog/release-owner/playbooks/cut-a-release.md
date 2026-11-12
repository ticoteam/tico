# Cut a release

Triggered by a task asking for a named release, or by a person asking for one. Budget 60 minutes. The outcome is one release ready for the owner: a readiness
checklist with a go or no-go, the notes, a suggested version, and an ask for the owner's OK. Nothing is tagged until the owner OKs
that exact version.

Engineering tasks waiting in Release need none of this: a person already signed each off in QA, and that is the OK.
Cut their release with `tico task release <id> --version N` (with `--tag <version>` where the repository releases from a
tag), once for the repository; every task waiting on it moves on when the release is published. The daily release hour's
task is the same: ask once for a task in Release, then close the hour's task. Where the stage has no "Release with" set
(a person's setting; never ask to change it), ask for nothing and tag nothing: the tasks wait for the next release.

---

## 1. Read the task

    tico task show <id>

List the tasks it names. Leave out any task you cannot open, and never ask for its name. The notes cover the tasks you can read.

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
for any change that might be breaking. The notes must cover every task you could read; a task with no merged pull request in the
range is listed under "Could not classify" with the question.

## 5. Ask for the OK

    tico task ask <id> --to <owner> "Publish release 1.4.2 at commit abc1234? <the readiness recommendation in a few words>" --kind ok --version N

The ask names one version at one commit, as `playbooks/rollout.md` step 0 describes. Put the readiness report and the
notes' SHA-256 in a comment on the task. A publish goes through only on that OK.

## 6. Roll out, only after the OK

Once `tico task show <id>` shows the owner's OK on that exact version and commit, follow `playbooks/rollout.md` from its step 1
in one run. Any other version or commit asks again. A failed step stops the rollout and goes to the owner as a question.

## 7. Finish

Update `knowledge/versioning.md` with the release, its date and commit. The task gets one
comment: the version and commit, the release link, the notes' path, the readiness outcome, and per install before and after with
its outcome. Then `tico task done <id> -m "..." --version N`, with the version in the first line.
