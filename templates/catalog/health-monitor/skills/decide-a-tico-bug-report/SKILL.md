---
name: decide-a-tico-bug-report
description: "Decide whether a finding is a Tico fault, file exactly one report for its signature, read the answer, and handle an approval or a refusal without working round it."
---
# Decide a Tico bug report

When to use: a finding of kind tico-bug, or a local finding that is one of Tico's own processes.

Steps:
1. Confirm it still holds with `hub health-monitor findings`, and check the kind. A Tico fault is something Tico does
   wrong: a route that is slow for everyone, a readiness report that is refused, a loop that busies a Tico process.
   A setup problem is never reported; it goes to BotOps (`skills/write-a-botops-fix-task/SKILL.md`).
2. Report it once: `hub health-monitor report <signature>`, with the `Signature:` value from the task (`hm-...`). Never the check name. For a local finding, use
   `--process <kind>:<name> --cpu <n> --mb <n>` from the task, where `<kind>` is `process` or `container` as the task's
   title says. A container keeps `container:`; it is never reported as `process:`.
3. Read the answer and act on it:
   - `ticket`: note the ticket on the task.
   - `comment: true`: the signature already had a ticket, and a seen-again note went on it. Note that.
   - `again: true`: nothing new to add. If it names an approval, that approval is still waiting for a person.
   - `approval`: a person approves it in Needs you, then `hub support send --approval <id>`. Wait to be woken.
4. A refusal (`support_off`, `support_busy`, `not_a_tico_bug`): write it on the task and mark it done. Do not reword the
   finding or retry to get round it.
5. Mark the task done with the ticket, the approval or the refusal in the note.

Checks before you finish:
- One report per signature. A repeat is a comment on the existing ticket, not a second report.
- Nothing you wrote was added to the report. The server builds it from the finding and the redacted bundle.
- A refused report was not reworded and sent again.
- The answer is on the task.

Example (invented, for illustration): a finding of kind tico-bug with signature hm-0a1b2c3d4e5f. The report answered
`comment: true`, so a seen-again note went on the existing ticket. The task says so, with the count, and no second
report was filed.
