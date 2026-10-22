### Changed
- **The stopped-run card is now just the decision.** Each stopped run shows one line on what stopped ("Stopped 8:12
  AM after using Bash."), one recommendation, and two buttons with the recommended one first: **Try again** leads
  only for a run Tico's own retry rule calls safe (it only read, on a host that reports every call, first stop);
  **Don't retry** leads for one that may have changed something, used an approval, left no full record or stopped
  more than once. The
  request, saved output, tool calls and the optional note are folded under **Details**. The Needs you line is
  shorter: what stopped and what waits on it. The review API adds `stopped` (`changed`, `read`, `twice` or `unknown`) and
  `acted` to each run.
