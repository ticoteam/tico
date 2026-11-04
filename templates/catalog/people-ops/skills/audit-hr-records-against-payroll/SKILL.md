---
name: audit-hr-records-against-payroll
description: Compare an HR export with a payroll export and the team roster field by field, list each mismatch with both values and dates, and leave the correction to a human.
---
# Audit HR records against payroll

When to use: when this week's tasks carry an HR export and a payroll export, and the weekly records check needs the comparison.

Steps:
1. Confirm both exports are dated and from this week. If either is missing or old, there is no audit this week. Say so.
2. Match people by reference, not by name. Where a reference is missing from one source, record it as "not in source", never as "no".
3. Compare these fields: title, manager, team, start date, employment status, and whether the person is active on payroll.
4. For each mismatch, write one row: reference, field, the HR value and its date, the payroll value and its date, the roster value,
   and the proposed correction, with the owner who should make it.
5. Put people on payroll who are not on the roster, or the reverse, at the top. A joiner not yet started may explain a gap; ask
   the HR owner rather than assuming.
6. Check documents on the record: a signed contract, or a right-to-work check where the law requires one. List what is missing
   by reference and type.
7. Do not pick a value silently. Show both values and let the HR owner or payroll correct the source. Where a gap looks like a
   process fault (for example, a team change never entered), name it as a process gap, not as a finding about the person.

Checks before you finish:
- Every mismatch shows both values with their dates.
- No value was corrected in either source, and no export is left in the working tree.
- Missing data is marked "not in source", not "no" or "zero".
- No salary, health, family, id or conduct detail appears on the page.

Example (generic, invented): Reference E-031, title field. HR record says "Support Specialist" (export 2026-09-27). Payroll says
"Support Agent" (export 2026-09-27). Roster says "Support Specialist" (`hub team show`, 2026-09-28). Proposed: payroll to correct
its title to match the roster, owner payroll contact.
