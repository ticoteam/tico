# Monthly help desk audit

Schedule: the first Monday of each month at 09:00 team time (routine `monthly-helpdesk-audit`), after setup. Budget 45 minutes.

Inputs: the latest configuration export or a read-only look at the help desk, a sample of the month's tickets, `knowledge/config-map.md`, `knowledge/tags.md`, `knowledge/change-log.md` and the Librarian's answers for any macro that states a policy.

Outcome: one page of findings, each with evidence and a change request with its own verification step.

Done when: the report is published; every finding has its evidence, and every change request has a check and an undo; last month's applied changes are recorded as worked or not worked; anything you could not read is named; and the task is done.

---

## 0. Check the date

If the routine woke you on a day that is not the first Monday of the month, finish the task with
"Not the first Monday; audit skipped" and stop. A run by hand on request always goes ahead.

## 1. Read the configuration

The latest export or a read-only look at: routing rules and triggers, automations, SLA policies,
macros, tags and views. Compare with `knowledge/config-map.md`; a rule that is new or changed since the
last audit gets a line, with who changed it if the tool shows it.

## 2. Check routing against intent

Take a sample of 30 to 50 tickets from the month (mail read, tasks or an export). For each, where it
landed and where `config-map.md` says it should have. Group the misroutes by the rule that fired.
Rules that conflict are a finding even without a misroute yet: two that take incompatible actions on the same ticket, or one
that undoes or blocks the other. Overlap alone is not a finding.

## 3. Check SLA policies against promises

For each promise in `knowledge/company.md` or a contract, the policy in the tool that times it: target,
business hours, which tickets it applies to. A promise with no policy, or a policy looser than the
promise, is a finding.

## 4. Check tags and macros

Tags used fewer than five times in 90 days, near-duplicates (`refund`, `refunds`, `refund_request`),
and tags off the naming convention. Macros not used in 90 days, macros whose text contradicts the
current doc (ask the Librarian with `hub doc ask`), and macros that set fields a rule also sets.

## 5. Write the change requests

One per fix, in the shape of `playbooks/write-a-change-request.md`. Order by tickets affected.

## 6. Write and hand over

Write `reports/YYYY-MM-DD-helpdesk-audit.md` in the shape of `knowledge/examples/helpdesk-audit.md` and
`hub file publish` it. Check last month's applied changes did what they promised and record it in
`knowledge/change-log.md`. Commit, and finish the task.
