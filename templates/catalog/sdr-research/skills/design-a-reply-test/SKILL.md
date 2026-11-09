---
name: design-a-reply-test
description: Design an outbound reply test with one changed variable, a randomised split of the same audience, a per-channel primary metric and guardrails, and propose it for a person's approval before it runs.
---
# Design a reply test

When to use: before changing an opener, a subject line, an offer, a sequence step or a channel, and before reporting whether a change worked.

Steps:
1. Write the hypothesis in one sentence: the change, the expected direction and the audience it applies to.
2. Name the control: the current version, run as it is now. Change one variable only.
3. Split the same audience slice 50/50 at random before anything is sent. Do not compare this week's audience with last week's.
4. Set the primary metric: confirmed positive replies divided by unique messages sent, at a fixed window (for example, 7 days). Report each channel separately; never blend channels.
5. Set the guardrails: opt-out rate, wrong-person rate, bounce rate and spam complaints. Set the stop rule before the test starts (for example, stop an arm early if its opt-out rate is twice the control's).
6. Count a reply as positive only when it confirms interest in information, a conversation or a meeting. Opt-outs, wrong-person replies, auto-replies and irrelevant replies are not positive.
7. Write the test as a proposal on the task. It does not run until a person approves it.

Checks before you finish:
- One variable is changed, and the control is named.
- The denominator is messages sent, not drafted, queued or enrolled.
- The window is fixed in advance, and the readout comes after it.
- Each channel has its own number, and the guardrails are reported beside the primary metric.

Example (invented, for illustration): "Hypothesis: a local-count opener lifts positive replies over the current opener for accounting firms in one region. Control: current opener. Split: 50/50 of the same 400 contacts. Primary: positive replies per unique email sent, 7 days. Guardrails: opt-outs, wrong-person, bounces. Stop an arm early if its opt-outs reach twice the control's. Proposed, not launched."
