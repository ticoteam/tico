---
name: decide-whether-an-action-is-a-task
description: Decide whether something said in a meeting is an action item that gets a task, using the commitment test, a named owner and a stated date, search for an existing open task first, and attach to that task rather than create a second one for the same need.
---
# Decide whether an action is a task

When to use: for each candidate action item, before a task is created or proposed.

Steps:
1. Apply the commitment test. An action item is a specific thing that a named person said they would do ("I will send the
   draft"), or that they were asked to do and agreed to. A suggestion ("we should look at pricing"), an idea, or a group
   statement ("the team will") is not an action item. Write it as an open question, or drop it.
2. Name the owner from the transcript label. If the owner is unclear, write "owner unclear" and ask the human who ran the
   meeting. Never assign the item to whoever spoke last or to whoever is most senior.
3. Take the due date only when someone said one. Otherwise write "not said". Do not turn "soon" or "next week" into a date.
4. Search for an existing task: `hub task list --owner <person>`, then look for the item's words among the open tasks. If
   one exists, link it, and add the meeting as its source with `hub task comment`. Do not create a second task for the
   same need.
5. If the item is new and passes steps 1 to 3, create the task with `hub task create --owner <person> --link <meeting
   link>`, with the quote and timestamp in the body. If it does not pass, propose it on the write-up with its gap named.
6. If the item needs a person who is not in the task, do not file a new ask. Leave the original task with its owner,
   `hub task update <id> --status waiting --on <owner>`, and say in one line what it waits for.
7. Add the row to `knowledge/actions.md`: the item, owner, date (or "not said"), task id and status.

Checks before you finish:
- Each action item passes the commitment test, and its quote supports it.
- Each owner comes from a transcript label, or is "owner unclear".
- No due date appears that the transcript did not state.
- No second task exists for a need that already has one.

Example (invented, for illustration): "Action: send the revised price sheet to the two trial studios. Owner: Ravi Okafor,
who said 'I'll send it' at 00:31:12. Due: not said. Existing task: none found for Ravi Okafor. Task created with the meeting
link. Not an action: 'we should look at the trial data' at 00:44:02 has no owner, so it is an open question."
