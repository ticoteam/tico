### Fixed
- **A run whose only work was a git push counts as acting.** The computer reports the pushes a run made from its
  task's worktrees with its result, so in `hub sql` the run reads `acted` with `did_nothing` 0 instead of
  `no_change`. Pushes from another task's worktree are not counted, and older runs read as before.
