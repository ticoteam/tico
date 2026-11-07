---
name: ask-a-channel-owner-for-work
description: Ask a channel Owner for a quote, scoping, data or a check for one segment, as one task with one outcome, one date and a stated spend limit, without starting a test or spending money, then track the ask until it is answered.
---
# Ask a channel Owner for work

When to use: when this segment needs channel work before a test can be planned, such as a quote, an audience check, a keyword list, a data pull or a page check. Not for starting a test. The Marketing Team Manager picks tests.

Steps:
1. Find the channel and its Owner with `hub growth channel list`, which names each channel's owner. Check the stage with `hub growth channel show <slug>`. Never assume a slug. A paused channel takes no ask; ask the Marketing Team Manager instead.
2. Check `knowledge/asks.md`. Keep to two open asks per channel Owner from this segment at a time, and one open ask per channel per idea. If that is full, wait, or say so on the idea.
3. Write the ask in five lines: the one result wanted for this segment, the date needed, the spend it may use (none, or an amount within the segment's budget), the message version it relates to, and the one thing you will do with the answer.
4. File it as a subtask of the idea it serves, with the channel and segment labels:
   `hub task create --owner <channel owner slug> --title "<the ask, one line>" --body-file <file> --parent <idea task id> --label channel:<slug> --label segment:<slug>`
   If there is no idea yet, file it on the segment's own task and say so.
5. An ask with money in it needs a spend approval first, and does not start until it is approved: `hub approval request --kind spend --payload-file <file> --task <ask task id>`.
6. Record the ask in `knowledge/asks.md`: the date, the Owner, the task id, the outcome wanted and its state (open, answered or closed).
7. If there is no answer after seven days, add a reminder as a comment on the same ask: `hub task comment <id> "<reminder>"`. After 14 days, ask the Marketing Team Manager on the task.
8. On the answer, record it in `knowledge/asks.md` with its date and the number it gave. An answer that costs money goes to the growth meeting as an idea. It does not start from the ask.

Checks before you finish:
- The ask names one outcome, one segment, one date and a spend limit.
- Nothing in the ask starts a test or changes a channel.
- The Owner comes from `hub growth channel list`.

Example (invented, for illustration): "Ask to the Search Ads Owner: 'Quote two more creator-only ad groups for November.' Outcome: a quote for a monthly spend between $800 and $1,000, with expected new creators per month from the Owner's own forecast. Needed by 2026-10-29. Spend in this ask: none. Filed as a subtask of idea 4399."
