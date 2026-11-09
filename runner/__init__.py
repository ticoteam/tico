"""The module paths that saved units and earlier releases run, forwarded to runner_support/.

The Python helpers moved to runner_support/ (the Rust runner, runner-rs/, is the runner). What still names runner/:
- launchd plists and systemd units, those earlier `scripts/tico install` runs wrote and those this release writes:
  `python -m runner --config <path> run|connectors|close-calls|importers` (runner/__main__.py);
- cloud machine images, saved by earlier releases and by this one: `python -m runner.cloud_machine boot`;
- a runner binary of an earlier release, still running while its checkout moves to this one: `python -m runner
  --config <path> <side job>|catalog`, `python -m runner.engine prepare` and `python -m runner.release_update`;
- the update process of an earlier release: it prepares the new tree's runner with `python -m runner.engine prepare`,
  and only when runner/engine.py exists;
- processes of an earlier release still running on the moved checkout (the server, side jobs, the update process),
  whose lazy imports (`from .redact import ...`, `from runner.importers.base import ...`) now load from here.

Every module of runner_support/ has a file here. `import runner.<name>` gives the runner_support module itself (the
file swaps itself out of sys.modules); `python -m runner.<name>` runs runner_support's as `python -m
runner_support.<name>` would. macOS turns started by an earlier binary source shell_init/ by path: its files forward too.

Units are still written with the old names (scripts/tico-computer, runner_support/systemd_units.py render,
runner_support/cloud_machine.py unit_text) so that a computer moved back to a release from before the move still starts.
So runner/__main__.py and runner/cloud_machine.py stay until a later release rewrites the saved units to
`runner_support` and no supported target release predates the move. The rest can go once no supported release predates
it.
"""
