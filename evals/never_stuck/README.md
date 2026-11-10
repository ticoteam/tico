# Never-stuck fault rig

Runs the real task engine (migrations, `move()`, `waiting_on`, `never_stuck.check`) on a fake clock with fake bots and
people, and breaks things on purpose. No HTTP, no browser, no wall clock: one `random.Random(seed)` drives a run and
every id comes from it, so a seed always replays the same way.

```
python -m evals.never_stuck.run --seed 7 --explain          # one seed, every step printed
python -m evals.never_stuck.run --seeds 1-200 -j 2          # a sweep
python -m evals.never_stuck.run --seeds 1-10000 --shard 3/12 -j 4 --allow-known --json   # one release shard
python -m evals.never_stuck.run --scenarios                 # the 52 spec scenarios, the bugs, the extra checks
python -m evals.never_stuck.perf --tasks 10000              # one check pass over 10,000 open tasks
```

- `world.py`: a disposable hub copied from a template (General and the Work pipeline: a by-hand stage, limits, an
  expected time, a people-only OK, a stage that cannot be pushed past), owner bots, a manager bot, three people. Every
  statement goes through a counting connection that can crash a write part way.
- `sim.py`: one seeded run. Bots finish, stop early, comment and stop, crash (failed, interrupted, expired), ask a
  person, snooze with and without an end, file a sub-task, never answer. People answer, OK, say no, send back, give
  it to someone, push past with a reason, stay silent. Faults: a crash between any two statements of a move or a check
  pass, lost wakes, bots paused, quarantined or out of credit and then restored, two movers at once, clock jumps
  forward and a small jump back, a reassign mid-run, a linked task deleted. After every step `rules_hold` is empty and
  every open task has a `waiting_on`. At the end the faults stop and the ladder drains: every task must end Done,
  Closed, Snoozed, by hand, on a person's turn, or behind tasks that do. No task may use more than 7 bot runs without
  a change before a person is asked.
- `scenarios.py`: the 52 scenarios of the Pipelines v3 spec ("Scenarios"; build plan in
  `docs/dev/pipelines-v3-build.md`), each built or listed in `LATER` with its phase and why; `FAILING` names the ones
  an engine bug breaks.
- `run.py`: the command line. `perf.py`: the 10,000-task timing.

`backend/tests/test_never_stuck_rig.py` runs every scenario and a few seeds in about 15 s (`-n 2`). A scenario or seed that
fails on a known bug is a strict expected failure: when the bug is fixed the test turns red, and the mark comes off.

## Known engine bugs

| Name | Seen in | What happens |
| --- | --- | --- |
| `runs-outside-ladder` | seeds 23, 27, 49, 52; bug case | The 7-run bound counts the ladder's own wakes only. An unsnooze, a Late wake, an entry, ask or answer wake to a bot without the turn, or a wake queued before the last change is a bot run on top of it. |
| `lost-wake-is-a-try` | scenario 3 | A lost wake is found only after the 2-minute gap and counts as try 1 ("You stopped before this task was finished"). The spec says the next pass, and no try. |
| `bot-closes-persons-request` | scenario 12 | A bot closes a task a person asked for, with a reason, and the engine accepts it (C8). |
| `mention-not-in-needs` | scenario 43 | An @ of a person on a task puts nothing in their Needs you (C28 is not built). |
| `file-into-not-run` | bug case | A stage's `file_into` saves, but a move into that stage raises NotImplementedError instead of filing or refusing. |

A sweep tags each failure with the bug it matches, so a new one stands out as `[NEW]`; `--allow-known` makes only new
ones fail the run. When a bug is fixed, drop it from `sim.KNOWN_BUGS` or `scenarios.FAILING`.
