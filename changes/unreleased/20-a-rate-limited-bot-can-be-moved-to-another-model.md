### Fixed
- **A rate-limited bot can be moved to another model.** Settings > Bots refused the change with "Wait for the
  current bot turn to finish" whenever a retry was running, so the bot stayed stuck on its spent model. The change now
  applies at once and takes effect from the next turn; the limit lifts, and a turn still running on the old model no
  longer puts the limit back on the new one.
