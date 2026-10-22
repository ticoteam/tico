### Fixed
- **A task blocked only by its own quiet subtask no longer waits silently.** Relating a subtask as its parent's
  blocker returns a warning, and when that subtask (a bot's hold or acknowledgement) goes quiet waiting on nothing
  else, the parent counts as stalled even while waiting: its bot is woken with the way out, and BotOps sees it stuck.
