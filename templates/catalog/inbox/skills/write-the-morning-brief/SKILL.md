---
name: write-the-morning-brief
description: Write the morning mail brief with a headline count, what needs the human today, the drafts ready, the routed items, what would be filed, and what could not be read, using message ids and one-line reasons and no bodies.
---
# Write the morning brief

When to use: at the end of each morning pass, and for the first brief during setup.

Steps:
1. Run the rules as `playbooks/morning-mail-brief.md` says, and read the untriaged list. Count what the rules settled.
2. Open with a headline of counts: read, settled by rules, needs you, drafts ready, routed. One sentence.
3. List "Needs you today" first, oldest deadline first. Each line has the message id and a reason that points at the message.
4. List the drafts with their ids, the sender, the ask in a few words, and whether a gap is marked.
5. List the routed items with the owner they went to and the task id. Do not flag them as needing the human.
6. List what would be filed, with its count and ids, or what was filed if Filing is on. No bodies, no address lists, no quoted history.
7. End with "Could not read": each read that failed or was refused, named by its refusal. A blocked mailbox is never "nothing found".

Checks before you finish:
- The headline counts match the lists below it.
- Every flag carries a message id and a reason.
- No message body, quoted history or address list is in the brief.
- A failed read is named, not reported as an empty inbox.

Example (generic, invented): "Headline: 41 in the inbox, the rules settle 29, 3 need you today, 4 drafts are ready, 2 routed." The first line under Needs you gives an id and "asks for a signature by Thursday". The close reads "Could not read: nothing. The full inbox was read at 07:31."
