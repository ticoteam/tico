# Sources

The text of this skill is original. These are the works whose ideas it adapts. All were read on 2026-11-18.

| Source | Licence | What was drawn from it |
| --- | --- | --- |
| Matt Pocock skills, `diagnosing-bugs`. https://github.com/mattpocock/skills | MIT | Build a tight feedback loop first and make it red-capable, deterministic, fast and runnable by the agent; no red-capable command, no next phase; raise the rate of an intermittent failure; shrink until every element is needed; three to five ranked, falsifiable hypotheses; one variable at a time; tag debug logs with one unique marker; state the confirmed hypothesis in the PR. |
| superpowers, `systematic-debugging`. https://github.com/obra/superpowers | MIT | No fix without a root cause; check recent changes; log at each boundary of a multi-part system and trace the bad value back to its source; after three failed fixes stop and question the design before a fourth. |
| pstack, `bug-fix` playbook and poteto-mode principles. https://github.com/backnotprop/pstack | MIT | Every shipped line traces to runtime evidence; nothing ships "just in case"; undo what a refuted guess added; verify on the same surface; a guard is a fix only when the state is legitimate; when fixes sharing one premise fail, test the premise. |
| addyosmani agent-skills, `debugging-and-error-recovery`. https://github.com/addyosmani/agent-skills | MIT | Bisecting between a good and a bad commit with a repro command. |
| Anthropic knowledge-work-plugins, `incident-response`. https://github.com/anthropics/knowledge-work-plugins | Apache-2.0 | Four severity levels with response times; every update says what is happening, who is affected, what is being done and when the next update is; status words; timeline kept as the incident runs. |
| GitHub awesome-copilot, `incident-postmortem`. https://github.com/github/awesome-copilot | MIT | Blameless review; causes marked confirmed, suspected or ruled out; time to detect kept apart from time to mitigate; every action is Detect, Mitigate or Prevent; owners are individuals; vague actions are broken into concrete ones. |
| Tico `skills/honest-code`; catalog `site-reliability-engineer/skills/check-an-all-clear`, `count-impact-from-sources`, `write-an-action-item`; `docs/releasing.md` | Tico | A pass on retry is a failure; stop when repeated fixes rest on one guess; impact counted from sources; the all clear on evidence; action items with owner, date and done-when; the four hotfix cases. |

Numbers that can go stale: the first-response times are Anthropic's values as read on 2026-11-18. The update rhythm,
the two working day review deadline and the "three failed fixes" stop are starting values for a team to tune.

Not legal advice: the section on personal data and security says only that notice duties exist and vary, and sends
the decision to the owner and a lawyer.
