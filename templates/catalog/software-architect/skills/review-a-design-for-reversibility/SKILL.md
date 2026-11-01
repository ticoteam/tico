---
name: review-a-design-for-reversibility
description: Sort each decision in a design doc as a two-way door (cheap to undo) or a one-way door (costly to undo), then check the blast radius, the data path and the rollback of each one-way door before the design is approved.
---
# Review a design for reversibility

When to use: in every design doc or RFC review, after the problem is restated and before the options are judged. It decides
how much scrutiny each part of the design gets.

Steps:
1. List the decisions in the proposal, one line each. A decision is a choice that others will build on: a data model, an
   interface, a vendor, a queue, an ownership boundary, a default.
2. Mark each as a two-way door (a flag, a module boundary, a configuration value, a choice behind an interface) or a one-way
   door (stored data others will read, a public contract, a vendor's data format, a deleted field, a choice that customers
   have seen). Mark a decision one-way when undoing it would need a data migration, a customer message or a second team's
   change.
3. For each one-way door, answer four questions and write the answers in the review:
   - Blast radius: which services, stores, customers or teams change if the decision is wrong?
   - Data path: who writes the data, who reads it, and what the readers do with each value?
   - Rollback: what the previous state needs to run again, and how long it takes. If there is no rollback, say so.
   - Expand and contract: can the change ship as a new form beside the old one, with the old one removed later?
4. For each two-way door, confirm it is reversible in practice: the flag exists, the module can be replaced, the value can
   be changed without a migration. If not, it is a one-way door.
5. Put the one-way doors first in the review and name the decision-maker for each. A review that spends the same effort on
   a flag and on a data format has not done the job.
6. State the verdict in the review's first line, using the ladder in `knowledge/architecture-practice.md`: approved, approved
   with changes (each change names its replacement), or rework (only when the shape is wrong).

Checks before you finish:
- Every decision is marked one-way or two-way, with the reason.
- Every one-way door has its four answers, or a stated gap.
- Each data reader in the design has been traced for at least two example rows, not only the one the design names.
- The verdict in the first line matches the one-way doors found.

Example (invented, for illustration): "Decision: store reminder channel as an enum in the bookings table. One-way: the column
is read by the export and by a partner's feed. Blast radius: export and the partner feed. Rollback: the old text column stays
until the partner confirms. Expand and contract: yes, a new column first. Verdict: approved with changes, the partner feed
change named as a precondition."
