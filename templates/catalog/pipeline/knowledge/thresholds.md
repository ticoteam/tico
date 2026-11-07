# Thresholds

The lines the report uses to call a measure slow, stalled or too frequent. These are starting defaults. Setup
replaces each with the team's own choice, with the date and the reason. A change is made here, with the date and the reason,
never in the middle of a report.

- **Merge wait slow:** a median merge wait over four hours in the window.
- **Merges stalled:** any stretch of more than one working day in which a gating check stayed red on the default branch.
- **Failed deploys too many:** more than one failed deploy in ten deploys in the window.
- **Rollback:** every rollback is reported. Two or more in one window is flagged for the owner.
- **Release cadence slow:** no release reached users for more than twice the usual gap between the last releases.

## Sources

- 2026-11-07: starting defaults from this template. Not team data. Setup records the team's choice here with the date.
