---
name: write-a-patch-plan-in-merge-order
description: Turn the ranked advisories into a patch plan an engineer can merge in one sitting, grouping the upgrades that share a lockfile change, ordering by tier and effort, flagging major bumps and naming the check and the rollback for each step.
---
# Write a patch plan in merge order

When to use: after the advisories are ranked and before the weekly report or a task hands the fixes to an engineer.

Steps:
1. Re-query each advisory against the exact installed version in every lockfile at the time of the run. A lockfile read from
   last week can say "same versions" and miss advisories published since. Record the date of the query.
2. Group the upgrades that land in one change: the same lockfile, the same parent package, or the same runtime. Three alerts
   fixed by one bump are one step.
3. Order the steps by tier first (from the policy), then by effort (the smallest safe step first inside a tier), and put
   any step that must come before another first. Mark a major-version bump that needs a code change, and name the call site
   that changes.
4. For each step write: the package, from and to versions, the parent to bump if transitive, the advisory identifiers, the owner
   from `knowledge/exposure.md`, the deadline from `knowledge/patch-policy.md`, and the check that proves the fix (the test
   suite, a start-up check, a request to the endpoint that was reachable).
5. Write the rollback: the previous lockfile revision, and the step that restores it. Say if the step cannot be rolled back.
6. If no fixed version exists, write the mitigation instead, with its owner and the date to revisit it.
7. Mark each step's state: planned, merged, blocked, or not checked. A step that could not be checked is never written as done.

Checks before you finish:
- Each advisory appears in exactly one step, or in "no fixed version".
- Each step names its owner, its deadline, its check and its rollback.
- Major bumps and their code changes are flagged.
- The query date is on the plan.
- Anything that could not be read appears as "not checked", not as clear.

Example (invented, for illustration): "Step 1 (tier 1, owner Priya, deadline 2026-10-01): api, `imgparse` 2.3.1 to 2.3.4, advisory
GHSA-xxxx-0001. Check: the upload test suite and a request to the avatar endpoint. Rollback: revert the lockfile to the 2026-09-28
revision. Step 2 (tier 2, owner Priya): `jwtlite` 1.8.0 to 2.0.1, major: `decode()` changes signature in `auth/tokens.py:30`."
