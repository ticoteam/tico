# Validate in production

Used when a Product task you own enters Validate: its Engineering work is done. Budget 45 minutes. The outcome is one
"Checked in production" page attached to the stage's requirement: the verdict first, the version production runs, each
acceptance criterion with its screenshot or measurement and its result, and what could not be checked. Jev checks it.
You do the checking and bring the proof; a person never does the QA. Do not ask a person to test the change, look at
it or send a screenshot: what you could not check goes on the page under "Could not check".

---

## 1. Read what was promised

    tico task show <id>

Under "From earlier stages" it lists the OK'd Spec page (`page:<file id>@<version>`; read it with
`tico file read <file id>`) and the pull request of each Engineering task it filed, with the release each shipped in.
Read the spec's acceptance criteria and their check tags. If a pull request says "in no release yet", production does
not run it: go to step 2.

## 2. Check the version production runs

Find the version production runs now, from the release record or the product's own version line. If it is older than
the release that carries this change, production does not run it yet. Wait for the release, not for a clock time.

When production is the Tico server your company runs on (the change is in its own repository), make the task depend on
the Engineering task that ships the change, until it is released,

    tico task relate <id> depends_on <Engineering task> --until released

and stop. The task shows Blocked by that task, with no date. When production runs a release that holds that task's
merged pull requests, you are woken once with the version it runs; check again then. One dependency for each Engineering
task whose change production does not run yet.

Any other product's production version is not seen by Tico, and that command refuses its tasks ("merged nothing into
... whose release this server runs"). Snooze to the day you will read its version again, a day or two out,
`tico task snooze <id> --until <date> -m "look again on <date>: read the version production runs" --version N`, and stop.
Check again then; if production still runs an older version than the one that carries the change, snooze again the same
way.

## 3. Check each criterion in production

Read `skills/build-the-page/SKILL.md` first. For each criterion tagged before release, check it in production
and keep its proof:
- **A screen**: open production in the read-only view with `tico bot app-shot` at phone (390 px) and desktop (1280 px)
  width, and keep the screenshots. A size or a position is measured from the screenshot, and the number written down.
- **No screen** (an API, a command, a job): run it or read it on the version production runs, and keep the call and its
  output.
- **The pull request's checks**: give gh the pull request's link, or `-R <owner/name>` with its number: a
  `git archive` copy has no remote to find the repository from.

      gh pr checks <pull request link>
      gh run list -R <owner/name> --commit <merge sha>
      gh run view <run id> -R <owner/name> --log-failed

  Your GitHub access reads pull requests, checks, statuses and workflow runs; it never writes them.

Write met, not met or not checked for each, with its proof. A criterion you cannot check (the read-only view is off, a
screen only another person sees and no test account shows it) is "not checked" with the reason; it is never met. For
each criterion tagged after launch, write who watches it, for how long, and the read date from the spec's "How we will
know".

## 4. Build the page and attach it

Follow `skills/build-the-page/SKILL.md` with `templates/validation.html`: the verdict and its count first, then what
to decide, then each criterion with its result and proof and the screenshots at both widths with red numbered arrows,
then the details: the version checked, what could not be checked and the after-launch checks. Write
`reports/YYYY-MM-DD-checked-<slug>.html`, build and check it with `page.py`, publish it on the task (it prints the
file id), render the published page with `page.py render` and look at it, then attach it:

    tico page publish reports/YYYY-MM-DD-checked-<slug>.html --title "Checked in production: <the change>" --task <id>
    tico task attach <id> page:<file id> --req "<requirement>" --version N

Then end the turn: Jev checks the page against the spec. When it sends the page back, its note says what is missing:
check again, publish a new version and attach it.

## 5. Not validated

A criterion that is not met in production files new work: move the task back with what is wrong,
`tico task move <id> "In development" -m "<the criterion and what production does instead>" --version N`. That is a new
visit, so a new Engineering task is filed.

## 6. After the read date

On each read date in the spec, read the measure with the export it names, record it in `knowledge/briefs.md` and in the
weekly review, and say whether it held. Do not call a measure held from a cohort younger than its window.
