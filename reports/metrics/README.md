# Verification metrics

`scripts/metrics.py` recomputes verification metrics from GitHub and git history.
`.github/workflows/metrics.yml` runs Mondays at 15:00 UTC and on demand, writing
a job summary and an HTML artifact. Nothing is committed weekly. Use `--weeks N`
(default 12) for the window including the current ISO week.

Red → green episodes on main count Release validation episodes in the week of
green completion. Episodes still red are shown as open, by red-completion week,
including any open episode that began before the window. The weekly table also
shows merged PRs into main and closed episodes per 100 merges; weeks without
merges show “—” for the rate. Open episodes are excluded from that rate.

Post-release reverts and hotfixes count once per originating PR in the UTC
month of its first fix merge or manual discovery, including late discoveries.
The originating merge must be contained in a release tag predating that evidence.
Lightweight tags have no creation date; release publication supplies a conservative
bound. Retained GitHub runs limit red-main history. Unattributed fixes are listed
for review; explicit revert/hotfix PR or commit references supply attribution.
Every commit query checks local existence first. Missing SHAs count as not
shipped; unmapped revert/hotfix PRs and manual origins are listed for review.

Keep hand-added JSONL only for evidence GitHub cannot reconstruct. Both files
below merge into post-release reverts and hotfixes after the same shipped check,
deduped by originating PR across all history before applying the window. Times
are UTC ISO 8601; `commit` is a full originating SHA; `pr` is its PR number (or
null to resolve from `commit`).

| File | Fields | Records |
| --- | --- | --- |
| `escapes.jsonl` | `timestamp`, `commit`, `pr`, `test`, `selected_tests`, `selection_reason`, `run_url` | Late hand-added post-release report; `timestamp` is discovery time. |
| `green_but_wrong.jsonl` | `timestamp`, `commit`, `pr`, `description`, `evidence_url`, `fix_commit` | Incorrect shipped behavior despite green checks; `timestamp` is discovery time. |

Either file accepts optional `"confirmed_customer": true`. The separate
“confirmed customer reports” line counts each shipped originating PR once, in
the window of its earliest confirmed record's timestamp, even when its first
revert or hotfix was earlier. Confirmation does not change the first-discovery
timestamp used by post-release reverts and hotfixes.

Record repository paths and public evidence, without customer text or credentials.
The page emits only identities and counts, never descriptions or evidence text.
