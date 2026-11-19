# Blast radius

The lines a PR edits are easy to read. The damage usually happens somewhere else: in code that relied on the old
behaviour and that a search for the changed name does not show. This page is how to find that, and how to say how
sure you are.

## 1. Name the one fact

Every change is safe because of something. Write it as one sentence that could be false:

- "Nothing outside this module reads `status` directly."
- "This endpoint is only reachable after sign-in."
- "Every old row has a value in this column."
- "The mobile app ignores fields it does not know."

If you cannot name the fact, you do not yet understand the change. Read more before judging it.

## 2. Prove it, and say which rung you reached

| Rung | What you did | Worth |
| --- | --- | --- |
| 1 | Someone said so (the author, a comment, your own memory) | Nothing. Do not report at this rung. |
| 2 | Pointed at the line: quoted `file:line` that shows it | Enough for a `question:` or a plain `issue:` |
| 3 | Walked the failure: followed the bad input step by step and showed where it stops or lands | Enough for a blocking finding when the path is short and quoted |
| 4 | Ran it: a script or test that calls the real code and fails loudly when the fact is false | Enough for any finding |
| 5 | Reproduced it in the running app, the way a customer would meet it | Strongest; use for high-risk parts |

Go as high as the risk deserves. For sign-in, money, personal data and deletes, aim for rung 4 or 5. Write the rung
next to the fact in the review. "Unproven" is an honest answer and must be written when it is true.

## 3. Look where search stops

A search for the changed name finds direct callers in the same language. These are the places it misses:

- **The library underneath.** Read the source of the exact pinned version, not the docs for the newest one.
- **Timing and shutdown.** What runs while this runs? What happens if the process stops halfway? What runs twice on
  a retry?
- **Data at rest.** Old rows written before this change. Empty and missing values. The column's real type and
  limits.
- **Data on the wire.** The JSON an API returns, a webhook body, a file format, a queue message. Anyone who saved or
  parses the old shape.
- **Another language reading the same bytes.** A mobile app, a script, a report, a query in a dashboard.
- **Names built at run time.** Strings joined into a field name, a route, a job name, a config key.
- **Feature flags and config.** The code path with the flag off, and with it on for only some customers.
- **Three hops away.** The caller of the caller of the caller. Follow one path all the way to a screen or a stored
  record.
- **Old clients.** Versions of an app or a connected service released before this change that still talk to the new
  server.
- **Jobs and schedules.** Background jobs, Routines and exports that read what this change writes.

## 4. Write it up

Put this block in the Review page:

```
What the change does:      one or two lines, in plain words.
The one fact:              the sentence from step 1.
Proven:                    rung <n>, by <command or file:line>   |   unproven, because <reason>.
Risks:                     for each: how it breaks, where (file:line), how likely, what it costs, how to check.
Cleared:                   what you checked and found safe, with the evidence.
Before you merge:          the cheapest single test that would catch the worst risk.
```

Keep the risks list short and ranked. A risk with no way to check it is a worry, not a finding; leave it out or ask
it as a `question:`.
