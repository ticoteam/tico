---
name: check-a-cloud-resource-for-waste
description: Decide whether a cloud resource is idle or oversized before anyone proposes a cut. Use a window long enough for the claim, measure at the level where cost is incurred, rule out production dependencies, and count a saving only once the next bill shows it.
---
# Check a cloud resource for waste

When to use: before a cloud resource is named as idle, oversized or a cut candidate, and whenever a saving is claimed.

Steps:
1. Name the resource, the account or project it sits in, its owner on record and its purpose. If the purpose is unknown, ask the owner first.
2. Measure at the level where the cost is incurred. For a virtual machine that is host CPU and network, not the CPU of a container running inside it.
   Name the metric and its level in the note.
3. Choose the window. For idle: at least 14 days of network in and out and of connections. For sizing: at least 60 days of CPU, using the
   peak and a high percentile, not the average. Compare complete periods only, and weekday with weekday.
4. Leave out a recent deliberate change. After an upsize, wait a full window before proposing a reversal.
5. Check for a production dependency: a caller in the service map or code, a place in a deploy or runbook, a failover role. A resource
   that others depend on is not a cut candidate, however idle it looks.
6. Size the saving from the billing. Say whether the resource is billed on demand, under a commitment, or a mix, because a commitment may not
   change the rate that removing usage saves. State the basis.
7. Propose one action to the owner with its evidence, labelled as a suggestion. Count the saving as expected until the next bill shows it.
   You never cancel or resize anything yourself.

Checks before you finish:
- The window is stated, and it is long enough for the claim.
- The metric and its level are named.
- The production-dependency check is written down.
- The saving is stated as "expected until the next bill".

Example (invented, for illustration): "Analytics batch host (owner: data lead). Host CPU peak 4 percent over 60 days; network under 1 MB a
day for 21 days; no callers in the service map. Candidate for a smaller size; suggestion only. Billed on demand. Saving counts when the
next bill shows it."
