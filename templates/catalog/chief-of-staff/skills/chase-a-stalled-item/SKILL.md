---
name: chase-a-stalled-item
description: Find who owns the next step of a stalled goal or task, draft one short nudge, and log it, without asking the same person about the same item twice in a week.
---
# Chase a stalled item

When to use: a goal or task has had no change past the stalled threshold in `knowledge/rhythm.md`.

Steps:
1. Find the owner of the next step with `hub goal show <id>` or `hub task show <id>`. If no owner is named, say so on
   the item and ask the human who owns it, once.
2. Check `knowledge/open-loops.md`. If you already asked this person about this item this week, do not ask again.
   Say so in the brief.
3. Draft one nudge of one or two sentences. It names the item, how long it has been quiet, and the one thing you
   need from the person.
4. Send it only when the task asks for it and a granted Tool allows it (`hub message send --fyi <person> "..."`),
   and within the limit of ten unsolicited messages to a human a day. Otherwise leave it as a draft under the item
   in the brief.
5. Add a line to `knowledge/open-loops.md`: the item, who was asked, the date, and what came back or "no reply yet".
6. At the next brief, check the loop again. A loop with no answer after two asks goes into Needs you as a question
   for the owner.

Checks before you finish:
- The nudge names the item and the thing needed, and nothing more.
- No person was asked twice about the same item in one week.
- Nothing was sent unless the task asked for it and a Tool allowed it.
- The loop is logged in `knowledge/open-loops.md` with today's date.

Example (generic, invented): The launch post has been in review since 2026-10-09 with no change for twelve days. The
draft nudge to the marketing lead reads: "The launch post has been in review for twelve days. Who is the reviewer,
and can it go out this week?" It stays a draft in the brief, and the loop is logged with today's date.
