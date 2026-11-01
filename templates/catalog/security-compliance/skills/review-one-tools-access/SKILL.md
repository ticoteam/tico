---
name: review-one-tools-access
description: Mark each account in one tool's export for a quarterly access review (admin, left, role changed, dormant, shared or service, looks right), give the reason for every mark, and propose keep or revoke for the owner to decide, without changing anything in the tool.
---
# Review one tool's access

When to use: when a quarterly access review reaches a tool, before any line is marked or any revocation is proposed.

Steps:
1. Record the export's date and the tool. If the export is older than the quarter's first day, ask for a new one and stop.
2. Match each account to a person on the roster from `hub team show`. Match on the person, not the display name. An account that matches no one is marked "unknown owner" and the owner is asked.
3. Mark admin accounts first. Each admin gets its own line and the reason the role is needed. Keep them at the top of the table.
4. Mark **left** when an active account belongs to someone not on the roster. Put it at the top with the admins, and tell the Operations Manager at once, as `AGENT.md` says.
5. Mark **role changed** when the role is broader than the person's current job on the roster, or than the owner's rule in `knowledge/controls.md`.
6. Mark **dormant** when the last login is older than the threshold in `knowledge/controls.md` (the playbook default is 45 days). Where the tool does not show logins, mark "no login data" and say so. Never call such an account dormant.
7. Mark **shared or service account** when it has no named owner in `knowledge/controls.md`. No owner is a finding.
8. Mark **looks right** only when no other mark applies. Give the basis, for example "role matches job, last login last week".
9. For each line that is not "looks right", propose keep or revoke with its reason. Leave the owner decision column blank until the owner answers.
10. Count the table. Every account in the export appears once, under one mark.

Checks before you finish:
- Every account in the export appears once, with one mark.
- Every mark other than "looks right" has a reason.
- The dormant threshold is stated on the table.
- Nothing in the tool was changed. A revocation is a proposal to the owner and a task, unless a task asks for it and a granted admin Tool allows it.
- The review is not complete until a fresh export shows the changes.

Example (generic): "Tool C, export dated 2026-10-02, 41 accounts. Admins: 2, at the top, each with its reason. Left: 1, a person off the roster since 2026-09-12, proposed for revoke and escalated. Dormant: 3, no login for 60 days against a 45-day threshold, proposed for revoke. Shared: 1 mailbox with no named owner, a finding. Looks right: 34. Owner decisions pending on 4 revoke proposals, and an owner is needed for the mailbox."
