# Writing policy

- Lead with the outcome. Then what the reader must do, if anything.
- Short sentences. No filler, no restating the request.
- Task titles: verb first, under 70 characters. "Investigate organic traffic drop", not "SEO stuff".
- Comments: only when they change what someone should do next. An @ in a comment only marks the task unread
  for that person; when you need them, ask them on the task (`tico task ask`).
- Task notes and asks are plain sentences a teammate reads once: what happened, what is next, what you
  need. No raw IDs, hashes, operation IDs or counters ("split519475db", "count10 op…"); name the thing
  in words and give at most one link to it.
- Address an ask to a real person or role on the roster (`tico team show`). Never to a name that is not
  there, such as an outside operator or "Root"; when nobody on the roster can act, say so to the requester.
- One ask per need. When the question changes, ask the new one: it replaces the old, unanswered one.
- Reports go in your repo under `reports/YYYY-MM-DD-<slug>.md`. When a person is to read one,
  attach it to the task (`tico task attach <task-id> reports/<file>.md`) and put the link it prints
  in a comment. A path on your Mac or an `s3://` URI is not a link anyone can open.
- An ask to a person (the sentence of `tico task ask`, or the first paragraph of a task you give them) is one or
  two plain sentences: what you need and what happens after ("OK the video tool trial for Thursday? If yes,
  I'll send the invite."). Tico refuses one with ids, hashes, paths, `s3://` links or tool names.
- Durable learnings go in `memory/learnings.md`. Decisions and their reasons in `memory/decisions.md`.
- Shared knowledge goes to the hub via PR to `docs/`.
- Follow `documentation.md`: bot-local docs are autonomous, ordinary shared internal `docs/` PRs
  may be self-merged, and external docs stop in one reusable pull request for the owner to review.

## Rules and the log

`AGENTS.md` and `policies/*.md` hold only current rules: present tense, no dates, no history, no
measurements. When a rule changes, replace it rather than appending an exception. Every dated
decision and its reason goes to the log: `memory/decisions.md` for one bot, the decision log for shared rules; newest first, one line each, `YYYY-MM-DD — <decision> — <reason>`. A number
that justifies a rule goes in the log; the rule states the threshold.
