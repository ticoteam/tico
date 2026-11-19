# The rule table

One table, kept at `knowledge/gates.md`, pairs every engineering rule the team relies on with the thing that enforces
it. It answers two questions at a glance: "what stops this mistake?" and "which rules are still only words?"

## The table

```
| Rule | Mistake it stops | Evidence (at least two) | Level | Enforced by | Status | Added | Last recurred |
```

- **Rule**: one sentence, stated as what to do. "Decide whether to charge only through `billing.is_billable()`."
- **Mistake it stops**: what goes wrong without it, in terms of what a customer or teammate sees.
- **Evidence**: links to the PRs, tasks or incident reviews where it happened.
- **Level**: 1 Design, 2 Types / lint / CI, 3 Tests, 4 Written rule.
- **Enforced by**: the exact thing. A module boundary, a type name, a lint rule id, a script path and the CI job
  that runs it, a test name, or the file and heading of the written rule.
- **Status**: one of the values below.
- **Added**: the date the row was added.
- **Last recurred**: the date of the newest instance since the gate landed, or "never".

## Status values

| Status | Meaning |
| --- | --- |
| open | Class found (two or more instances), nothing enforces it yet |
| in progress | A PR is open |
| enforced | The gate is merged and was proven on a real past instance |
| ratchet | Enforced for new cases; old cases remain, with a baseline that only goes down |
| judgment | Cannot be checked automatically; lives as a written rule (Level 4) |
| retired | The mistake can no longer happen, or the rule no longer applies; row kept one cycle, then removed |

## Rules for keeping it

1. A row is added only for a class with at least two instances.
2. A row is not marked "enforced" until the proof exists: the check failing on a real past instance and passing on
   current code.
3. When a row becomes "enforced" or "ratchet", the same rule is deleted from the agent instruction file and from
   `knowledge/standards.md`. The table is the only place it is still written.
4. On every run, fill "Last recurred". A rule that recurred after its gate landed is moved up a level or its gate is
   fixed.
5. "judgment" rows are reread on every run with one question: could an automatic check do this now? Most can, later.
6. The table should get shorter. Count the rows at each status on every run and put the counts in the Corrections
   page.

## Choosing the level: quick questions

Ask in order and stop at the first "yes".

1. Could the code be shaped so the wrong thing cannot be written at all? (One way to do it, old way deleted,
   internals hidden, one source of truth.) Level 1.
2. Could a type refuse the bad state, or could a script find the bad pattern by reading the source? Level 2.
3. Does the mistake only show when the code runs? Level 3, a test at a real seam.
4. Does it take judgment that no check can make (naming, tone of a message, whether a feature is worth building)?
   Level 4.

## What a good gate error looks like

```
<what was found, with file and line>
<what to use instead: the function, type or file>
<one line of why, pointing at the rule table row>
```

Illustration (invented):

```
jobs/retry.py:57 selects subscriptions to charge with its own query.
Use billing.is_billable(subscription) from billing/rules.py.
Direct queries have skipped the pause check twice. See knowledge/gates.md, row "charge only through is_billable".
```

## The exception marker

On the offending line, never in a config file:

```
<allow marker for the rule>  reason: <why this case is different>  until: <date>  approved: <teammate>
```

The check reads the date and fails once it has passed.

## An example table (invented)

| Rule | Mistake it stops | Evidence | Level | Enforced by | Status | Added | Last recurred |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Decide whether to charge only through `billing.is_billable()` | Paused customers charged | PR 231, incident task 530, PR 244 | 1 | Charging columns readable only inside `billing/` | enforced | 2026-11-18 | never |
| Every API route declares a permission | Data visible without the right | PRs 219, 226, 240, 247 | 2 | `scripts/check_routes.py` in the lint job | enforced | 2026-11-18 | never |
| Format dates with `fmt.date()` | Dates shown in mixed formats | six review comments | 2 | lint rule `no-raw-date-format`, baseline 140 | ratchet | 2026-11-18 | never |
| Error messages tell the customer what to do next | Dead-end error screens | PRs 222, 238 | 4 | `knowledge/standards.md`, "Error messages" | judgment | 2026-11-18 | 2026-11-12 |
