# Weekly dependency and advisory report

Schedule: Mondays at 08:00 team time (routine `weekly-security-report`), after setup. Also run by hand. Budget 45 minutes. The outcome is one page: what to patch first and why, in
the order to merge it. Nothing is dismissed, merged or changed.

Inputs: `knowledge/patch-policy.md`, `knowledge/exposure.md`, the open Dependabot pull requests, and the alert list when this bot's access allows it, and any advisory a
person named on a task. Done when every in-scope repository is read or named as not checked, each alert has a tier with
its reason, the patch plan is in merge order, and the report is published with `--scope task` if it names an unpatched flaw.

---

## 1. Read where things stand

    hub task show <id>

Then `knowledge/patch-policy.md`, `knowledge/exposure.md`, `knowledge/ledger.md` and last week's report. Check
whether last week's plan was merged (`gh pr list -R <repo> --state merged --search "merged:>YYYY-MM-DD"`).

## 2. Collect the alerts

For each repository in scope: open Dependabot pull requests and the advisories they name, plus any
advisory a human handed you on a task. For each: package, installed and fixed versions, GHSA or CVE, the
advisory's severity. Re-query each advisory against the version installed now. If the alert list cannot be read, write
"alerts not checked" at the top of the report and name the access needed. Never report zero alerts for a source you could not read.

## 3. Rank each one

Follow `playbooks/assess-an-advisory.md` and its reach method in `skills/trace-reachability-of-a-vulnerable-call/SKILL.md`. Tier 1: on CISA's Known Exploited Vulnerabilities list, or a
high EPSS probability (0.1 or more) and reachable on an internet-facing service. Tier 2: reachable, or
critical and reach unknown. Tier 3: everything else, including dev-only and test-only packages. Each line
carries its reason.

## 4. Check for committed credentials

Search open pull requests' diffs and the default branch for credential patterns (private keys, cloud and
API tokens, `.env` files) in a read-only clone, following `skills/handle-a-committed-secret/SKILL.md`. Report each by file,
commit and credential kind only, and the rotation step. Never the value. A scan that could not run is "not checked", never "none found".

## 5. Write the patch plan

Follow `skills/write-a-patch-plan-in-merge-order/SKILL.md`.

Group upgrades that land together (one lockfile change fixing three alerts), order by tier then by effort,
and flag major-version bumps that need a code change. Name the owner from `knowledge/exposure.md` and the
deadline from the policy.

## 6. Write and hand over

Write `reports/YYYY-MM-DD-security-report.md` in the shape of `knowledge/examples/security-report.md`,
`hub file publish` it with `--scope task`, and create a task for the owner of each tier 1 item the same day (`hub task create --owner <owner>`). Update `knowledge/ledger.md`, commit, then `hub task update <id> --status done --note`.
