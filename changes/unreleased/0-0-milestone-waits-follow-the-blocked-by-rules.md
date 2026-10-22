### Fixed
- **Milestone waits follow the blocked_by rules.** Adding or removing a wait for a pull request or release now needs
  the same permission as a blocked_by (the task's owner, or a person on a team that moves tasks), a deleted task
  keeps its waits and gets them back when restored, and a message held while a task waits says what it waits for.
