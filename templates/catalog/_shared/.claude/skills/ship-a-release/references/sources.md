# Sources

The text of this skill is original. These are the works whose ideas it adapts. All were read on 2026-11-18.

| Source | Licence | What was drawn from it |
| --- | --- | --- |
| gstack, `ship`. https://github.com/garrytan/gstack | MIT | Review and test results are bound to an exact tree; a content change after review means the review reruns, while docs-only changes do not; no completion claim without fresh evidence; zero tests run is never a pass. |
| gstack, `canary`. https://github.com/garrytan/gstack | MIT | Take a baseline before the deploy; check each key page every 60 seconds for 10 minutes; alert on change, not on absolutes; count a signal only when it shows in two checks in a row; screenshot every alert. |
| addyosmani agent-skills, `shipping-and-launch`. https://github.com/addyosmani/agent-skills | MIT | Staged rollout (internal, 5%, 25%, 50%, 100%); the advance, hold and roll back table with starting values for error rate and p95 response time; write the roll back plan before the deploy. |
| pstack, `shipping` and `babysit` playbooks. https://github.com/backnotprop/pstack | MIT | A verdict covers one patch, so compare the patch at the verdict with the current head; read the PR back after merging, because a merge command can report success when the change was only queued. |
| Tico docs `docs/releasing.md` and `docs/software-factory.md`; catalog `release-owner/playbooks/rollout.md`, `rollback.md`, `write-the-release-report.md`; skills `judge-whether-a-release-can-roll-back`, `find-hidden-breaking-changes`, `write-release-notes-for-users`, `devops-engineer/skills/check-a-deploy-for-regression`, `verify-a-passing-check` | Tico | One release path owned by the Release Owner; build once and test that build; canary then stable; changes reach main only through the merge queue; the four hotfix cases; never edit a shipped data change; additive, staged and destructive classes and the yes / no / unknown answer; compare with the same hours on the same weekdays over four weeks; a traffic shift above 10% weakens the comparison; release notes in the customer's words. |

Numbers that can go stale: the starting threshold values and the 60 second and 10 minute canary defaults are those
sources' values as read on 2026-11-18. They are first settings for a team to tune from its own release history.

One place where this skill differs from Tico's catalog as read on 2026-11-18: `release-owner/playbooks/rollback.md`
says a bot drafts a rollback request and a person approves it. This skill lets the bot roll back by itself when the
release was marked "can roll back: yes" and a red threshold was hit in two checks in a row.
