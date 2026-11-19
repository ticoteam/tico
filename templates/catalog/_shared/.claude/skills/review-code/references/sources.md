# Sources

The text of this skill is original. These are the works whose ideas it adapts. All were read on 2026-11-18.

| Source | Licence | What was drawn from it |
| --- | --- | --- |
| pstack, `interrogate`. https://github.com/backnotprop/pstack | MIT | Other models attack the change; a finding survives only when a run proves it; each finding gives the command, the output and the output the intent requires; verify by rerunning; merge same-cause findings; report the dropped count; no findings is a valid result; report, do not fix. |
| pstack, `blast-radius`. https://github.com/backnotprop/pstack | MIT | The one fact a change is safe because of; the five-rung confidence ladder; looking where search stops; the hand-back format. |
| pstack, `shipping` playbook. https://github.com/backnotprop/pstack | MIT | Only a verdict from someone who did not write the code counts; green CI is not a verdict; a verdict covers one patch; text in comments is data, not instructions. |
| gstack, `review`. https://github.com/garrytan/gstack | MIT | The blocking floor (would fail a test, break production, or lose data or secrets); quote the line or the finding is unverified; scope drift against stated intent; listing every edit to tests, CI and lint config on its own; the sideways sweep of other call sites and of new enum values. |
| Matt Pocock skills, `code-review`. https://github.com/mattpocock/skills | MIT | Spec and standards as two separate passes that are never merged; quote the spec line; skip what tooling enforces. |
| addyosmani agent-skills, `code-review-and-quality`. https://github.com/addyosmani/agent-skills | MIT | Flip one condition the change adds, run the tests, restore; still green means a test is missing. |
| Anthropic claude-plugins-official, `code-review` command. https://github.com/anthropics/claude-plugins-official | Apache-2.0 | Validate each finding in a separate pass; drop pre-existing issues, pedantic nitpicks and anything a linter catches. |
| Tico docs `docs/software-factory.md`; catalog `reviewer/playbooks/review-a-pull-request.md`, `reviewer/skills/map-the-risk-in-a-diff`, `reviewer/skills/write-a-review-comment-the-author-can-act-on`, `devops-engineer/skills/verify-a-passing-check` | Tico | Reviewers advise and the author decides; no second round of the same review; second review as a check-model child task; the Review page named by task and commit; the 15 minute budget; the risk surface list; comment labels; more than three blocking findings is a design conversation; the five security hold classes. |
