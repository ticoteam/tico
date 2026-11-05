---
name: hand-off-feedback-evidence
description: Send a theme, a bug or a request to the team that acts on it, with the count, the window, the quotes, the exclusions and what is unknown, so the receiver can decide without re-reading the sources.
---
# Hand off feedback evidence

When to use: when a theme, a bug or a named request needs another team to act, within the requested work.

Steps:
1. Name the receiver by what the evidence is. A named feature request, and the accounts behind it, goes to
   `product-ops`. A reproducible bug goes to `issue-triage`. A question for discovery goes to `product-researcher`. A usage
   question goes to `product-analyst`. A docs gap goes to the Librarian as a task. If no bot fits, name a person.
2. Write the hand-off in one message on a task. Start with the claim in one line, stated as what customers say, not what
   to build. Then give: the count this week and last, with its noise note; the severity mix; two anonymised quotes with
   source and date; the segment weight, if one was documented; what was excluded and why; what could not be read.
3. Ask the one question the receiver can answer, for example "Is this the same cause as the sync issue?"
4. Attach the log lines, not the raw tickets. Remove personal details first.
5. Label any suggested fix as a suggestion. Do not file on the receiver's board unless the task asks for it.
6. Record the hand-off in the report's actions with the receiver and the date.

Checks before you finish:
- The receiver can act without opening the source.
- Every number has its window.
- No name, account id, or quote with an identifying detail.
- A suggestion is marked as one.

Example (generic): "Claim: customers in two plans say the export drops their last row. Count: 6 this week, 2 last week (both
windows fully read). Noise: the difference is about 1.4 times the noise (the square root of 8 is 2.8), so a watch item. Quotes: two, dated. Excluded: none.
Not read: the chat channel. Question for issue-triage: can someone reproduce the dropped row on the export from last Tuesday?"
