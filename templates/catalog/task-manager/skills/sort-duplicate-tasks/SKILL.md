---
name: sort-duplicate-tasks
description: "Relate two open tasks with the same title, requester and owner as duplicates when their work is the same, keep anything the newer one holds on the older one, then close the newer one. Comment instead when they may hold different work."
---
# Sort duplicate tasks

When to use: a "Duplicate titles" item: two open tasks share a title, requester and owner.

Steps:
1. Read both in one call: `hub task show <id> <id>`. Compare the bodies, the statuses, the last messages and the links.
2. Same work and same ask: relate the newer task to the older as a duplicate:
   `hub task relate <newer id> <older id> --kind duplicate_of`. The older task is the one you keep.
3. Before you close the newer task, copy onto the older one anything it has that the older lacks: a link, a reply, a
   progress note. Comment on the older task with the newer task's id and what you copied.
4. Close the newer task with the note the report gives: `hub task close <newer id> --note "Duplicate of <older id>"`.
5. Different work under one title (other bodies, other links, other answers): do not relate or close them. Comment on
   the newer task with the other task's id and what differs, so a person can rename or close one of them.
6. Note on the check task what you did for this item: related and closed, commented, or left.

Checks before you finish:
- Both tasks were read before any relate.
- The relate runs from the newer task to the older one, with `duplicate_of`.
- Anything the newer task held was copied to the older one before it was closed.
- Each close note names the older task's id. Each comment names the other task's id and what differs.
- Tasks with different work were not related or closed.

Example (invented, for illustration): two open tasks "Update the pricing page copy", same owner and requester, same
body, no links. The newer one was related to the older as a duplicate, its one reply was copied onto the older task, and
the newer one was closed with the note "Duplicate of <older id>". The check task says so.
