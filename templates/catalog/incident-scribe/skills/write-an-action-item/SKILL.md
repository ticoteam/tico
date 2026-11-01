---
name: write-an-action-item
description: Write an incident action item that someone can finish, with one specific system change, a proposed owner, a calendar due date, a done-when test and the evidence it comes from. Search existing tasks in every status before creating one, and prefer a fix to the system over a reminder to be careful.
---
# Write an action item

When to use: when a postmortem or review needs an action item, or a task asks for one to be created.

Steps:
1. Search before you write. Look for an existing task or action item on the same cause in every status (open, doing,
   waiting, done), with `hub task list --all` or a search on the cause. If one exists, link it and add the new
   evidence instead of making a duplicate.
2. Write the change in one sentence, as a system change: an alert, a guard, a test, a runbook line or a rollback
   step. "Be more careful" is not an action item. Rewrite it until it names something that can be built or checked.
3. Propose an owner by role or team, marked "proposed". Do not assign a named person on your own. The incident lead confirms.
4. Set a due date using the team default in `knowledge/triggers.md`, written as a calendar date (`YYYY-MM-DD`). If
   that file has no default, ask the incident lead.
5. Write the done-when test: the observable thing that shows the item is finished, such as "a migration with no lock
   timeout fails the template check" or "the alert names the table".
6. Attach the evidence: the timeline line or source that shows why the item exists.
7. Record it in `knowledge/actions.md` with the status "proposed". Create a task with
   `hub task create --owner <person> --title "<action item>" --parent <id>` only when the task asks for it and the owner is confirmed.

Checks before you finish:
- A search across every status was made, and any match is linked.
- The change is a system change with a done-when test.
- The owner is marked "proposed" unless the lead confirmed a name.
- The due date is a calendar date.
- The evidence line exists.

Example (invented, for illustration): The draft says "be more careful with migrations." Rewritten: "Add a lock timeout
to the migration template. Owner (proposed): the platform team. Due 2026-10-06. Done when a migration with no lock
timeout fails the template check. Evidence: timeline 14:02 to 14:44 UTC, lock wait on the bookings table." The search
found no open item on the same cause, so it is recorded in `knowledge/actions.md` as proposed.
