# Look up a topic

Triggered by a task asking what people are saying about something ("what do people think of our new
pricing?", "did anyone react to the competitor's launch?"). Budget 20 minutes. The outcome is one
short note on the task: what was read, what it says and what could not be read.

Inputs: the task, `knowledge/watchlist.md`, `knowledge/sources.md`, and `hub market show` for any organization the task names.
Done when: the note is on the task with the answer first, the coverage line (returned and blocked sources), and the quotes with their links; any finding that needs a person is proposed as a child task.

---

## 1. Read the request

    hub task show <id>

Name the topic, the window (default: the last 14 days) and who will read the answer. Ask once with
`hub task ask <id>` if the topic is ambiguous, and stop.

## 2. Query the sources

Use `knowledge/watchlist.md` and `knowledge/sources.md` for the queries that work on each source.
Read search results only; never click, type, submit or sign in. Note each source as returned or
blocked as you go.

## 3. Sort

Group what you found by what people said, not by source. Count only what you read. Quote the
few clearest lines with the link and date. Separate people asking a question, people complaining and
people comparing. Drop noise and duplicates.

## 4. Write the note

Answer first: "Mostly neutral; 4 of 9 threads ask how much it costs." Then coverage (returned and
blocked sources), the quotes with links, and anything that looks like a real move. No draft reply.

## 5. Finish

If a finding needs a human or the content bot, propose the child task in the note; create it only
when ready. Report competitor facts with `hub market report`. Commit, then `hub task update <id>
--status done --note`: the answer, then what was blocked.

## When a source fails

It goes in the coverage line as blocked. Unknown coverage is not "nothing found".
