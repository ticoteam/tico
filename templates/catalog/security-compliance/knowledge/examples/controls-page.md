A sample of excellent output for a fictional team. Every name in it is a stand-in.

```markdown
# Acme controls, October 2026 (written 2026-10-01)

Sample output for Acme, a fictional studio-software team in its first SOC 2 Type II observation
window (2026-07-01 to 2026-12-31). Every person and tool is invented. Nothing has been changed and
nothing has gone to the auditor. First draft, not yet reviewed.

**Headline: 3 of the 14 September controls have no file: 2 are missing (the change-review sample and
a leaver checklist) and 1 is claimed without a file (the backup restore test). 4 access reviews are
due this month.**

## September, a closed period
- **CM-02 change review sample, September. Missing.** Owner Kenji. Reference SOC 2 CC8. Branch
  protection requires one approving review on the main branch (repository settings, read
  2026-10-01). No list of September merges with their reviewer is filed. Needed: the list, exported
  from the repository with dates.
- **PL-03 leaver checklist, September. Missing.** Owner Dana. Reference SOC 2 CC6. One person left on
  2026-09-12 (team roster, read 2026-10-01). No checklist and no record that their accounts were
  removed. Needed: the signed checklist and the export showing the accounts gone.
- **BK-01 backup restore test, September. Claimed.** Owner Priya. Reference SOC 2 A1 (availability
  is in scope). Claimed done 2026-09-18 in a task comment. No restore log in the evidence folder.
  Needed: the restore log or a dated screenshot from the backup console.

September totals: 14 due, 11 collected, 1 claimed, 2 missing.

## Due this month
| Control | Evidence | Owner | Due | Reference |
|---|---|---|---|---|
| AC-01 quarterly access review | owner decisions and before and after exports, for 4 tools | tool owners | 2026-10-20 | SOC 2 CC6; ISO 27001:2022 A.5.18 |
| VM-01 vulnerability scan | scan report with its date and scope | Kenji | 2026-10-15 | SOC 2 CC7 |
| VD-01 vendor security reviews | a review for each vendor holding customer data, 2 new this quarter | Dana | 2026-10-31 | SOC 2 CC9 |

## Access reviews
- AC-01 covers 4 tools this quarter. Still open from Q3: 2 lines in the shared mailbox tool with no
  owner decision (owner Sam).
- The October review checks that the September leaver's accounts are gone. If one is still active,
  it goes to the Operations Manager at once.

## People
- 2 people who joined more than 14 days ago have not accepted the security policy: joined 2026-09-08
  and 2026-09-15 (acknowledgements, read 2026-10-01). One more joined 2026-09-22 and is not flagged
  yet.

## Vendors without a security review
- Vendor A, a form builder, holds customer sign-up data. No security review on file. The contract
  started 2026-08-03.

## Could not be read
- Backup console: this bot has no read access. BK-01 rests on the task comment above.
- CRM export for AC-01: dated 2026-09-20, before the quarter began. A new export is requested from
  its owner, Sam.

## Sources
- controls.md, evidence-log.md, repository settings, acknowledgements.md, team roster and the vendor
  register, read 2026-10-01
```
