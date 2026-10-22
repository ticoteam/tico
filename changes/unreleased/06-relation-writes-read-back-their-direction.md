### Added
- **Relation writes read back their direction**: `hub task relate`, `hub_task_relate`, task create with relations and
  `hub task wait` answer with what was stored, from both ends ("#237 waits for #233" / "#233 blocks #237"), so a
  reversed link is visible in the reply. `hub task wait --on task:<subtask>` warns about blocking a task on its own
  subtask, as `relate` and create do.
