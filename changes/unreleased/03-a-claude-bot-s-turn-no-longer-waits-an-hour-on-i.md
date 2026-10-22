### Added
- **A Claude bot's turn no longer waits an hour on its own background commands**: once the bot has answered and the
  turn is only waiting on background shells (a test suite, `gh pr checks --watch`, `gh run watch`) for 5 minutes
  (`background_wait_s` in bot.yaml, 0 for off), the runner ends the turn as completed and the commands keep running.
  When the last one ends, the bot gets one message on the same task with each command's exit code and the end of
  its output; one still running at the run's time limit is killed and says so. A new turn on that session meanwhile
  is told which commands still run. The run's events record "Turn ended: background wait" with the minutes saved,
  and the wake counts as `background_done` in `hub sql`. On a Mac the exit code reads "unknown". A turn still
  working, or running a subagent, waits as before.
