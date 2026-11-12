# Validate in production

Used when a Product task you own enters Validate: its Engineering work is done. Budget 30 minutes. The outcome is a
"Checked in production" page attached to the stage's requirement, naming the version production runs, which Jev checks.
No person OK is needed.

---

## 1. Read what was promised

    tico task show <id>

Under "From earlier stages" it lists the OK'd Spec page (`page:<file id>@<version>`; read it with
`tico file read <file id>`) and the pull request of each Engineering task it filed, with the release each shipped in.
Read the spec's acceptance criteria and their check tags. If a pull request says "in no release yet", production does
not run it: go to step 2's snooze.

## 2. Check the version production runs

Find the version production runs now, from the release record or the product's own version line. If it is older than
the release that carries this change, production does not run it yet. Wait for the release, not for a clock time: a
guessed release window or hour leaves the task asleep after the release lands. Snooze with the release in the reason and
a backstop date a day or two out,
`tico task snooze <id> --until <date> -m "waiting for <release>: production runs <version>" --version N`, and stop.
When production is the Tico server your company runs on (the change is in its own repository), its update to a new
release ends the snooze and wakes you at once. Check again then; if production still runs an older release than the
one that carries the change, snooze again the same way.

## 3. Check each criterion in production

For each criterion tagged before release, do it in production, or read the evidence that shows it (a test account, a
screenshot, a record), and write met or not met with the evidence. To read a pull request's CI, give gh the pull
request's link, or `-R <owner/name>` with its number: a `git archive` copy has no remote to find the repository from.

    gh pr checks <pull request link>
    gh run list -R <owner/name> --commit <merge sha>
    gh run view <run id> -R <owner/name> --log-failed

Your GitHub access reads pull requests, checks, statuses and workflow runs; it never writes them. For each criterion tagged after launch, write who
watches it, for how long, and the read date from the spec's "How we will know".

## 4. Write and attach the page

Write `reports/YYYY-MM-DD-checked-<slug>.md`: the version production runs and when it was read, each criterion with
met or not met and its evidence, the after-launch checks with their watchers and read dates, and what could not be checked.
Publish it with `tico file publish reports/YYYY-MM-DD-checked-<slug>.md --task <id>` (it prints the file id) and attach it:

    tico task attach <id> page:<file id> --req "<requirement>" --version N

## 5. Not validated

A criterion that is not met in production files new work: move the task back with what is wrong,
`tico task move <id> "In development" -m "<the criterion and what production does instead>" --version N`. That is a new
visit, so a new Engineering task is filed.

## 6. After the read date

On each read date in the spec, read the measure with the export it names, record it in `knowledge/briefs.md` and in the
weekly review, and say whether it held. Do not call a measure held from a cohort younger than its window.
