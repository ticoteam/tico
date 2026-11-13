# Weekday morning sweep

Schedule: `0 8 * * 1-5` in the team's timezone, routine `weekday-morning-sweep`. Take the run's
label from the task title.

Budget 20 minutes. The outcome is every due source read and saved with its status. You read and
save; you do not sort. Listening's rules decide what is kept, and its steps decide who gets it.
Quiet is a normal result.

---

## 1. Ask what is due

    tico task show <id>
    tico listening sources

`tico listening sources` (the `tico_listening_sources` tool) lists the search sources that are due
for a read, overdue first, each with its id, its site and its target (a search or a community).
Sources are the list. Anything not on it is not this sweep's work.

With nothing due, finish the task with that one line.

## 2. Read each source

Open each source's target on its site in your browser, in the order the list gives. Read the
results only. Never click, type, submit, or sign in anywhere. Read newest first, back to the last
sweep, at most 200 posts a source. `knowledge/sources.md` says how each site behaves.

For each post, note:

| Field | What it is |
|---|---|
| `native_id` | The site's own id for the post. When the site shows none, its permanent link |
| `url` | The post's link |
| `author` | The name shown on the post |
| `title` | The post's own title, when it has one |
| `text` | The post's words, as written |
| `posted_at` | When it was posted |

Take every post the target shows, not the ones you think matter. Redact personal details from
`text` before you save (the list is in `AGENTS.md`).

A read ends in one of four states:

| Status | When |
|---|---|
| `ok` | The site answered and you read it. No posts is still `ok` |
| `blocked` | A sign in wall, a challenge, or a page that refuses the browser |
| `rate_limited` | The site says to slow down or try later |
| `error` | Anything else that stopped the read |

A source that fails is saved with its status and you move on to the next one.

## 3. Save each source

    tico listening save --source <source id> --file <file.json>

One save per source (the `tico_listening_save` tool), as soon as it is read:

```json
{"status": "ok",
 "items": [{"native_id": "t3_1abc", "url": "https://forum.example/t/4412", "author": "studio_owner",
            "title": "Booking software that texts cancellations?", "text": "We run two studios and ...",
            "posted_at": "2026-09-28T16:20:00Z"}]}
```

A `blocked`, `rate_limited` or `error` read is saved with no items. Saving a post that was saved
before does nothing, so an overlap with the last sweep is safe. A save is refused while its
listening is off: skip that source and say so on the task.

## 4. Finish the task

Commit, then `tico task done <id> -m "..." --version N`: how many sources were read, how many posts
were saved, and each source that was not `ok`, by name, with what it refused with. Always finish
it. A scheduled task left open absorbs the next occurrence and quietly stops the sweep.

## When a source fails

It is saved as `blocked`, `rate_limited` or `error`, never as `ok` with no items. That means
unknown coverage for that source, not a clean read. Listening shows it on the source's dot. Two
failures in a row of the same source is one line on the task for the owner, not a repeated
complaint every day. Add how the site failed to `knowledge/sources.md`.
