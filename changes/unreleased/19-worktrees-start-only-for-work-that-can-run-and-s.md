### Fixed
- **Worktrees start only for work that can run, and stuck or obsolete ones free themselves.** A new task worktree is
  refused, with the reason, for a task not started yet, one blocked by an open task (named) or a finished one; a task
  waiting for room is checked again before its tree is reserved or prepared. A `pending` worktree whose pull requests
  all finished or whose task closed is cancelled (at once when nothing was checked out); one whose setup makes no
  progress for 6 hours fails visibly, tells its owner once and retries on the same worktree. A fix-forward after a merge
  keeps its tree while its bot's turn runs or its branch has commits past the merged head ("held: ..."). Each release
  records why, and Health lists them ([Task worktrees](docs/worktrees.md)).
