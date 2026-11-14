# Draft a rollback request

Used when the deploy record shows a bad release reached users and no good release has followed, or when a person asks for a
rollback on a task. Budget 20 minutes. The outcome is one drafted request, on a task, for the person who runs deploys. This
bot never runs a rollback on its own.

---

## 1. Read the deploy record

From the workflow runs and releases (`gh run list`, `gh release list`, `gh release view <tag>`), take:
- the first bad deploy: its run id, release tag, and time it reached users;
- the last good release: its tag and publish time;
- any release published after the bad one and before now.

If the logs are past retention or the runs cannot be read, say so. An unknown is not a good release.

## 2. Judge whether it can roll back

Read `skills/judge-whether-a-release-can-roll-back/SKILL.md`. State whether the earlier version can be restored as it is, and
whether data the new version wrote still works on the old one. If it cannot be restored cleanly, say what would be needed.

## 3. Draft the request

File it for your Manager, who checks it once and passes it to the person who runs deploys
(`tico task create --owner <your manager> --title "Roll back <repo> to <version>" --parent <id>`),
with, in this order:
1. the version to return to, and the commit it points at;
2. the evidence: the bad deploy's run id, the first failing check, the time it reached users;
3. the check that will show the rollback worked: the install's version after the update, and the workflow run that finished green;
4. the risk the judgement in step 2 found, in one line.

## 4. Stop there

Never run the rollback, and never run a rollout of an earlier version on your own. If the owner approves the earlier version on the
task, it is a rollout of that version under `playbooks/rollout.md`, with its own approval naming the version and commit. Otherwise it
stays a drafted request.

## 5. Record it

Count the rollback once, in the weekly report, at the deploy that made it, with the time to recover measured from the first bad deploy
to the first good one.
