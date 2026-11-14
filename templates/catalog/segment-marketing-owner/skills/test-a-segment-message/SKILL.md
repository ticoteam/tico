---
name: test-a-segment-message
description: Check a customer segment's message, offer and claims before any copy goes to a channel Owner or outside the team, so every claim is approved in the brand's claims with its source, the copy passes the brand check, and each idea states its change and measure without a prediction.
---
# Test a segment's message

When to use: before a message, offer or claim for the segment goes to a channel Owner, into an idea, or outside the team.

Steps:
1. Write the message in one sentence: who it is for, the problem in the customer's words, the offer (what they get, at what price or trial, if any) and the one next step.
2. List every factual claim in the copy: numbers, comparisons, "fastest", "only", customer counts, time to value and guarantees.
3. Check each claim against `tico brand show --section claims`. An approved claim is used with its source. A claim not in that list is not used. Ask the Design Owner (`design-owner`) on a task whether it can be added with its evidence, or rewrite the line without it.
4. Save the final copy to a file and run `tico brand check <file>`, or `tico brand check -` to read it from stdin. Fix each flag, or say on the task why a flag is wrong.
5. Check the offer against the segment's counting rule and activation step in `knowledge/segment.md`. If the offer promises something the product does not do for this segment, stop, and ask the Feature Owner for this segment on its task.
6. Write the idea's body with the segment, the change, the channel and the measure: the new customers and cost per customer from `skills/read-cost-per-customer-for-a-segment/SKILL.md`. Do not write a prediction, a hypothesis or a score. The Marketing task has no field for them.
7. Record the message's version, its date, and each claim with its check in `knowledge/message.md`.

Checks before you finish:
- Each claim is approved and sourced, or removed.
- The brand check has run on the final copy, and its flags are fixed or explained.
- The offer matches the segment's definition in `knowledge/segment.md`.

Example (invented, for illustration): "Creators: 'Set up your page in 10 minutes.' Approved in `tico brand show --section claims`, source: onboarding timing study, 2026-10-14. 'Used by 300 creators': not in the approved claims, so it is removed, and the Design Owner is asked on task 4406 whether it can be added with its source. Brand check on the final copy: no flags."
