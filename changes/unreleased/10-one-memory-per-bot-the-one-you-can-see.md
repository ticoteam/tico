### Changed
- **One memory per bot, the one you can see.** Bots no longer keep a hidden memory on the computer they run on
  (Claude Code's auto memory, Codex memories, the Gemini CLI's saved memories): each turn tells the bot to read and
  follow the working rules at the top of its `memory/learnings.md` and to save what it learns there, so what steers it
  shows in its Learnings panel and an edit there reaches its next turn. The panel's Learnings and Decisions views open
  on the working rules, then the newest entries ([Learnings](docs/learnings.md#one-memory-in-the-repository)).
