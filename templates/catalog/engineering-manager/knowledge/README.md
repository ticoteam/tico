# knowledge

This bot's own documentation of its domain. Not a report and not a log: the standing answer to
"what do we actually know about this?". Facts, how things work, who is who, what has been tried
and what came of it. Every bot repository has this directory. It is the first thing a fresh
session of this bot should read, and it is readable by any other bot that lists this repository
under `reads:` in its own `bot.yaml`.

It is built up run by run. Each run adds what it learned and corrects what turned out to be wrong,
so the directory gets more useful the longer the bot runs. Nothing in here is private: assume a
human and another bot will read it.

## How to write it

- **One topic per file**, named for the topic (`objections.md`, `pricing-questions.md`). Prefer
  updating an existing file over adding a new one; forty thin files are worse than ten good ones.
- **Plain markdown.** Short sections, real headings, no scoring tables or invented structure. Keep
  a file short enough to read in one sitting; when it outgrows that, split it along the topic.
- **Every file ends with a dated `## Sources` section**, one line per source that fed it, naming
  the conversation, the meeting, the task, or the document it came from, for example
  `- 2026-01-14: weekly sales call, 2026-01-13`. A fact that comes from one specific source
  carries its date inline too, so a reader can tell last week's fact from last year's.
- **When two sources disagree, record both with their dates.** Do not average them, do not pick a
  winner, do not quietly drop the older one.
- **Never invent.** If nobody said a number, there is no number. Write what the source said and put
  what nobody has answered in `open-questions.md`.
- **No credentials.** No tokens, keys, passwords, or credential values, not a fragment, not even inside
  a quoted log line.
- **No personal data beyond what the source already shows.** A person's name and employer are
  fine when they appear in the source. Never a home address, phone number, personal email address,
  card or bank detail, government id, or anything else about a private individual.

## knowledge/ and memory/

They are different things, and mixing them makes both useless.

- `memory/learnings.md` is about **how to do the job**: the flag you keep forgetting, the assumption
  that was wrong, what a tool refuses and why. Written for the next run of this bot.
- `knowledge/` is about **the domain**: what is true about the team, the market, the product.
  Written for anyone who needs to know the domain, whether a human, a future session, or another
  bot.

If it would still be true after this bot was replaced tomorrow, it belongs in `knowledge/`.
