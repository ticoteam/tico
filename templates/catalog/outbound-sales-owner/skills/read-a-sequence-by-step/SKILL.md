---
name: read-a-sequence-by-step
description: Read an outbound sequence by step and by messages sent, not in total or by clicks, with every sequence and channel on the same window and the guardrails beside each result, so that a change is judged on positive replies per send.
---
# Read a sequence by step

When to use: before you report a sequence's result, before you propose a change to it, and before you call one version better
than another.

Steps:
1. Fix the window in advance: the same number of days for each sequence, the last seven days and the seven before. Say so if a
   new list is being compared with an old one.
2. Use messages sent as the denominator, counted from `knowledge/sequences.md` and reconciled with the mail export. Drafted,
   queued and enrolled contacts are not sent.
3. Count by step: sends at step one, two, three and four; the replies that came after each step; and the step each reply came at.
   A sequence that loses people at step two needs a different step two, not only a different first line.
4. Count a positive reply only when it confirms interest in a conversation, information or a meeting. Opt-outs, wrong-person
   replies, auto-replies and "remove me" replies are not positive; they are counted under the guardrails.
5. Report each sequence on its own. Never add two sequences' numbers into one rate, and never blend two channels.
6. Put the guardrails beside each result: opt-out rate, bounce rate and spam complaints. A sequence over a guardrail is reported
   first, with that flag.
7. Call a difference only when both sides have enough sends in the window. The default floor is 200 sends per side. Under it, write
   "too few to call" and keep the test running.

Checks before you finish:
- The window is the same for every sequence, and it is stated.
- Each rate shows its denominator, messages sent.
- Positive replies are the confirmed kind, with the guardrails beside them.
- No difference is called under the send floor.

Example (invented, for illustration): "Sequence A, 14 days to 2026-11-02: 312 sends. By step: 210, 61, 41. Positive replies: 9, which
is 2.9 per 100 sends. Two replies were opt-outs and are counted under guardrails, not here. Guardrails: opt-outs 0.6%, bounces 1.9%,
complaints 0. Sequence B: 96 sends, too few to call."
