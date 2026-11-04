---
name: handle-a-detector-score
description: Use an AI-text detector's score on a finished draft as a noisy prompt to remove generic sentences and add missing facts, with a two-rewrite limit and a stop rule, and never as a target to tune.
---
# Handle a detector score

When to use: the team's AI-text detector scores a finished draft, or a playbook asks for a score before handover.

Steps:
1. Record the label, the score, the length of the text, and the rewrite count, beside the draft.
2. If the text is under about 75 words, note that the score is noisy and move on. A short text is not a third attempt.
3. Read the sentences the detector flagged, if the tool shows them. Look for the generic sentence: a tip any manager could sign, a definition the reader already knows, a closing line that repeats the opening.
4. Rewrite only those sentences, and only to add a checkable fact or to cut a generic line. Leave rhythm, vocabulary and punctuation alone. Changing them to move the score is not a rewrite the team wants.
5. Score again. If it still fails, one more rewrite of the same kind is allowed. That is the second rewrite, and the limit.
6. Stop. Drop the piece, or keep it as internal work and say so in the task. Do not ask a person to make it "sound human".
7. Whatever the score, run the claim check on the final text. A pass does not clear a claim.

Checks before you finish:
- The label, the score and the rewrite count are recorded beside the draft.
- No more than two detector-driven rewrites were made.
- No sentence was changed only to move the score.
- Any piece that failed after two rewrites is marked internal or dropped.

Example (generic): "Draft of 68 words, score noted as noisy. Two rewrites: one generic line cut, one fact added (the sample size). Second score still high; stopped. Piece kept as internal work, stated in the task note. The claim check still ran on the text."
