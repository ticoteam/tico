# Validate in production

Used when a Product task you own enters Validate: its Engineering work is done. Budget 30 minutes. The outcome is a
"Checked in production" page attached to the stage's requirement, naming the version production runs, which Jev checks.
No person OK is needed.

---

## 1. Read what was promised

    tico task show <id>

Read the OK'd Spec page the task links (`page:<file id>@<version>`), its acceptance criteria and their check tags, and the
Engineering tasks it filed with the release each shipped in.

## 2. Check the version production runs

Find the version production runs now, from the release record or the product's own version line. If it is older than
the release that carries this change, production does not run it yet: snooze to the next release date,
`tico task snooze <id> --until <date> -m "production runs <version>; the change ships in <release>" --version N`, and stop.

## 3. Check each criterion in production

For each criterion tagged before release, do it in production, or read the evidence that shows it (a test account, a
screenshot, a record), and write met or not met with the evidence. For each criterion tagged after launch, write who
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
