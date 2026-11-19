# Sources

The text of this skill is original. These are the works whose ideas it adapts. All were read on 2026-11-18.

| Source | Licence | What was drawn from it |
| --- | --- | --- |
| pstack, `create-verification-skill` and `maintain-verification-skill`. https://github.com/backnotprop/pstack | MIT | A verify skill kept in the product's repository with Launch, Doctor, Drive, Evidence, Cleanup and Helpers sections and one file per feature; prove it once end to end; real customer path, not internal setters; never weaken a journey to pass; tell a stale map from a product regression; edit only the skill's own folder. |
| gstack, `qa` and `qa/sections/exploratory.md`. https://github.com/garrytan/gstack | MIT | Severity tiers and which are fixed by default; one fix per commit with the regression test first; recheck the bug, the original flow and a neighbour; charters with a time or probe budget; show success before probing; write the guess before each probe; replay a failure from the same state before repair; a missing prerequisite or timeout is never a pass. |
| pstack, `feature` playbook. https://github.com/backnotprop/pstack | MIT | Verify on the matching surface; a wrong-surface or unclear result is not a pass. |
| Tico docs `docs/software-factory.md`, `docs/dev/pipelines-v3.md`, catalog `feature-owner/playbooks/qa.md` and `validate-in-production.md`, shared skill `build-the-page` | Tico | The QA and Validate stages; QA report and validation pages; real screenshots at 390 px and 1280 px with numbered arrows; a page never asks its reader to test; `tico bot app-shot` for read-only production looks. |

The session method in `charters.md` (charter, time box, session notes) follows common exploratory testing practice.
The table of odd conditions was written for this skill.

The limits (15 minutes or 12 probes, two to five charters) are starting values to tune. The 5 minute and 12 probe
figures come from gstack's smoke budget as read on 2026-11-18.
