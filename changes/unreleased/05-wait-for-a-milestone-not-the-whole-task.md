### Added
- **Wait for a milestone, not the whole task**: `hub task wait <id> --on pr:<url>` waits until that pull request
  merges, `--on release:v0.3.64` until that release is published (`task:<id>` is blocked_by). The owner gets one wake
  when it happens, while the task it came from keeps its own step (In QA, a release watch). A wait made after the
  merge, or a merge that arrived while the server was down, is woken once by the minute sweep; a replayed webhook
  never wakes it twice. The task page shows it under Related ("Waits for PR #943 to merge"). MCP `hub_task_wait`.
  A bot's subtask with a pending milestone wait is not a stuck hold: the task it blocks is not woken as stalled.
