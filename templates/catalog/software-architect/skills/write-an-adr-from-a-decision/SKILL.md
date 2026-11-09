---
name: write-an-adr-from-a-decision
description: Turn a decision made in a merged pull request, a design review or a meeting into a short architecture decision record, with the context, the options considered, the decision, its consequences, and a clear mark on anything inferred rather than stated.
---
# Write an ADR from a decision

When to use: when a significant decision was made and no record exists. A decision is significant when it meets the team's
rule in `knowledge/decision-rules.md`: a new service, data store or external dependency, a public interface change, or a
choice that would be expensive to reverse.

Steps:
1. Name the decision in one sentence, in the form "We will ..." or "We will not ...". If you cannot name it, the decision
   was not made, and that is the finding.
2. Write the context: the forces that made the choice necessary, in three to six sentences, with dates and the number of
   the pull request, meeting or design doc that holds the evidence.
3. List the options that were actually considered, at least two, including doing nothing. If the record shows only one
   option, write "Other options: none recorded" and do not invent them.
4. Write the decision and the main reason in one paragraph.
5. Write the consequences: what becomes easier, what becomes harder, what must now be done, and what is reversible. Include
   one negative consequence the team accepts.
6. Set the status: proposed until a named person accepts it, then accepted. A superseded record is kept and marked with the
   record that replaces it.
7. Mark anything you inferred. Use "(inferred from pull request #n)" beside it, so the owner can correct it before the record
   is accepted.
8. Keep it under a page. Name the file `adr/NNNN-<short-title>.md` with the next free number, in the repository's own format
   if it has one.

Checks before you finish:
- The decision is one sentence and matches what the source says.
- Each inferred item is marked.
- The status is proposed unless a named person accepted the decision.
- No secret, customer name or security detail appears in the record.

Example (invented, for illustration): "0014. We will use a managed search service for class lookup. Context: lookups time out at
peak (pull request #611, 2026-09-24). Options considered: self-hosted search (rejected: on-call cost, planning call 2026-09-22);
managed service (chosen). Consequence: index sync is a new job we must monitor. Status: proposed. Inferred: the cost comparison
came from the planning call, not a written estimate."
