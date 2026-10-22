### Changed
- **Questions close on their own** (Decide cards part 1). A bot's question to a person closes when its task or any task
  above it is done, closed or declined (pick questions too; a done task keeps only the "accept this?" question it asks
  its requester once done), when the person replies to it or writes on its task to that bot (the reply is the answer),
  and on its "if you don't answer by" date even when its fallback is a send, a spend or a delete (the bot is told nothing
  went ahead). One with no date that waits two business days (Mon-Fri, Pacific) goes back to its bot: each weekday
  morning a bot gets one "Still needed?" wake listing all of them, and asks again or drops them; until then it stays in
  Needs you, marked stale, rather than moving to Older questions. A stuck check never asks a person: the bot is retried
  once, then BotOps gets a task to find why, and open "no progress" questions to people close at the next hourly sweep.
