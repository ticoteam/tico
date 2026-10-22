### Fixed
- **Worktree release follow-ups.** A stuck `pending` worktree whose pull requests finished is released without first
  telling its bot to retry setup; only the turns of the bot that owns the task hold a finished tree; a fault reading
  turns is raised instead of read as "no live turn"; and a new push clears the earlier "stale push cleared" note, so a
  release no longer names an old push.
