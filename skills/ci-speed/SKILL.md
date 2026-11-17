---
name: ci-speed
description: Make a GitHub Actions pipeline faster with evidence. Find the jobs that hold up a merge, see where their minutes go, change the slow part, and prove the gain on the real runners. Read when asked why CI is slow, to speed up CI, or to check that a CI change helped.
---

# Faster CI, measured

The goal is less time from a push to a green required check, shown with numbers from real runs. Runner minutes on
jobs nobody waits for are a cost question, not a speed one; say which of the two you are saving.

This file lives in Tico at `$HUB_DIR/skills/ci-speed/SKILL.md`. In a person's own Claude Code it comes from the `tico`
plugin, and Tico is two folders up. `scripts/` and `references/` below are in this skill's folder.

## 1. Find what holds up the merge

1. Pick 5 to 10 recent pull request runs of the workflow that carries the required check:
   `gh run list --repo OWNER/REPO --workflow FILE.yml --event pull_request --limit 30`.
2. For each, `python3 scripts/job_times.py --repo OWNER/REPO --run RUN_ID` prints every job's queue wait, start,
   duration and slow steps.
3. The required check's `needs:` chain is the path a person waits on. Count how often each job finishes last
   across the runs. A job that never finishes last saves nobody time, however slow it is.
4. Look at a pull request's whole life too. If it mostly waits after its checks are green, faster CI is not the
   biggest lever. Say so.

## 2. See where a slow step's time goes

- Read a warm run's log with timestamps: `gh run view --repo OWNER/REPO --job JOB_ID --log`. Gaps between lines show
  where the time goes.
- Check every cache line first (`Cache restored from key`, `Cache not found`). Only a warm run is a baseline.
- A cache hit does not make a step fast. Cached `.deb` files still leave dpkg 25 to 45 seconds of unpacking. A
  restored virtualenv still has pip check every pin.
- Before removing work that looks unused, find what consumes it: grep the scripts the job runs. An "unused" browser
  install can turn out to feed a screenshot script.

## 3. Levers, cheapest first

| Lever | Use it when | What it bought (hosted ubuntu-24.04) |
|---|---|---|
| Run independent setup in the background | System packages, language packages and a build don't depend on each other | The slowest install hidden behind the others |
| `dpkg --force-unsafe-io` | Any apt install on a throwaway runner | dpkg 40–47 s down to 24–41 s |
| Split the longest step of a long-pole job into its own parallel job | That job often finishes last | A static-checks job went from 3m10s to 2m04s |
| Cache the installed files, not the packages | An apt install runs on every job | 24–47 s down to 3–13 s |
| Shallower checkout | `fetch-depth: 0` costs 15–20 s on a large repo | Only after checking everything that reads git history |

### Background setup

```bash
( set -euo pipefail
  sudo apt-get -o Dpkg::Options::=--force-unsafe-io install -y --no-install-recommends "${packages[@]}"
) > "$RUNNER_TEMP/system.log" 2>&1 &
system_pid=$!
# A failed attempt waits for dpkg, so a retry never meets its lock.
trap 'wait "$system_pid" 2>/dev/null || true' EXIT
# ... pip, npm, build ...
if ! wait "$system_pid"; then cat "$RUNNER_TEMP/system.log"; exit 1; fi
cat "$RUNNER_TEMP/system.log"
```

### Splitting a job

- Use the same `if:` as the job it leaves.
- Install only what the step needs.
- Add the new job to the required gate's `needs:` and to whatever checks each need's result.
- If a merge queue re-runs the static checks elsewhere, keep that list complete.
- Update the tests that pin the job list.

### Caching installed files

`references/snapshot.md` has the recipe. In short:
- The cache key names the runner image version, the package list and the install script.
- On a miss, apt installs and then a tarball is made of what changed. On a hit, it is `tar -xpf` and `ldconfig`.
- Check it once against a full scan, then launch the real program on a fresh runner.

## 4. Prove it on the real runner

- Benchmark with a throwaway workflow (`references/bench-workflow.yml`). It runs on push to its own branch, never in a
  pull request and never merged.
  - Run 3 or more repetitions per variant: the same step ranged from 0.6 s to 8 s on identical runners.
  - Time inside the step with `date +%s.%N`, and keep download separate from work.
  - Check correctness in the same job.
- Afterwards, delete the branch and its cache entries (`gh cache list --key PREFIX`, `gh cache delete ID`). A repo's
  caches share 10 GB.
- In the pull request itself, a change to a file inside a cache key makes that run cold, for every cache keyed on
  that file. After the cold run saves, rerun one job (`gh run rerun RUN_ID --job JOB_ID`) to see the warm path.
  Report both, labelled.

## 5. Report

- Give a before and after table, each number linked to its run, and mark what is measured and what is expected.
- Say whether each run was warm or cold.
- End with what is now slowest.

## Pitfalls

- Saving a cache fails with "tar exit code 2" when root-owned files are in the path. Run `chown` on it and drop apt's
  lock files first.
- dpkg keeps each packaged file's mtime, so find what an install wrote by ctime (`find -cnewer MARKER`), not mtime.
- `find /usr` over a hosted runner image takes 2 to 3 minutes. Scan named folders.
- `actions/cache` already compresses with zstd, so store a plain tar.
- Several `gh` pollers burn the API rate limit. Use one `gh pr checks --watch --interval 60`.
- Laptop timings are not CI timings. Measure on the runner the job uses.
