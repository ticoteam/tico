### Fixed
- **The scheduler no longer blocks the server for seconds at a time on a busy hub.** Its 10-second pass used to run
  in one write transaction and read every task to find due dates, so on a hub with many tasks and routines requests
  waited behind it and runners saw 503s. Each step, routine and batch of due-date reminders now commits on its own,
  the due-date and deferred-task lookups use new indexes (migration 61), and Health's scheduler entry shows the
  pass's longest single hold (`hold_ms`, with `hold_step`) and its longest wait behind other writers (`wait_ms`)
  next to `tick_ms`. These cover the 10-second pass; the minute and hourly sweeps take their own transactions.
