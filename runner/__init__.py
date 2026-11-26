"""The two legacy `runner` entry points, forwarded to runner_support/ until this package is deleted.

- `python -m runner ...` (runner/__main__.py): launchd plists and systemd units older installs wrote, which run `run`,
  `connectors`, `close-calls` and `importers`. New ones say `python -m runner_support`, and the runner's start moves old
  ones over (runner_support/release_update.py `move_at_start`).
- `python -m runner.cloud_machine boot` (runner/cloud_machine.py): cloud machine images saved before the boot unit said
  `python -m runner_support.cloud_machine boot`.

Every other module of runner_support/ is imported by its own name. The other `runner.<module>` forwarders were removed
because no supported release predates the move to runner_support/ (first shipped in v0.5.94).
"""
