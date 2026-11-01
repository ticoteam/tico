# Write a test plan

Triggered by a task naming a release, a milestone or a large pull request ("test plan for 2.15", "what should
we test before the billing change ships"). Budget 40 minutes. The outcome is one test plan and a regression
checklist a human can run in an afternoon. Nothing is run against production and nothing changes on GitHub.

---

## 1. Read what is changing

    hub task show <id>
    gh pr list -R <repo> --state merged --search "merged:>YYYY-MM-DD" --json number,title,files,labels
    gh pr view <n> -R <repo>

List each change in one line: what it does for a user, the files or area it touches, whether it has tests
of its own (look for changed test files) and whether it touches a risky area in `knowledge/areas.md`
(payments, auth, data export, migrations, anything with a past incident in `knowledge/themes.md`).

## 2. Rank by risk

Risk is likelihood of breaking times impact if it does. Put first: changes to money, sign-in, data loss or
migration paths; changes with no test files; areas with open bugs or a recent incident; changes that touch
more than one area. Say why each is ranked where it is, in one clause.

## 3. Write the cases

For the top risks, cases a human can follow without asking: preconditions, steps, the expected result, and
the negative case (wrong input, expired session, a second click). Keep automated checks to what CI already
runs; name gaps where a unit or integration test would catch the same thing earlier, as a suggestion for the
author. Aim for 10 to 25 cases, not 100. For each automated check you name, say whether it has been seen to fail on the
broken behaviour; a check never seen to fail is named as unproven.

## 4. The regression checklist

The short list of paths that must still work whatever changed: from `knowledge/regression.md` (start it from
the product's core flows if it does not exist), plus any flow a recent bug broke. One line each.

## 5. Hand over

Write `reports/test-plans/<release>.md`: headline (the three riskiest changes), the ranked table (change,
risk, why, cases), the cases, the checklist, and what you could not read. A case no one has run is marked not run,
never passed. Done when every change in the window is in the table or named as not read, each top risk has a
negative case, and the plan is published and attached to the task. `hub file publish` it, attach it
to the task, commit, then `hub task update <id> --status done --note`. A human runs the plan and records
the results; a failed case becomes a proposed issue in your next triage pass.
