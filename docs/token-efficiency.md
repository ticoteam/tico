# Token efficiency

Why Tico's bots once spent subscription limits much faster than a person using Claude Code or Codex directly, what was
changed, how to measure it, and what is still open. The Performance Owner keeps this page current and runs the weekly
review below.

## Measuring

**Unit:** input-equivalent tokens, weighted the way Anthropic prices them:

- fresh input ×1
- cache read ×0.1
- 1-hour cache write ×2
- output ×5

The headline number is **input-equivalent tokens per merged PR** from the engineering bots: Builder, Builder 2,
Reviewer 1, Reviewer 2, Engineering Manager, QA Owner and Release Owner. Per-run numbers alone mislead, because fewer,
larger runs are the point.

**Track alongside it:**

- **Plan drain:** share of the plan's weekly limit used per merged PR. This is what people feel.
- **Context:** median and p90 prompt size per model call (`prompt_tokens`), for all bots and for Builders.
- **Runs:**
  - input-equivalent tokens per run;
  - calls per run;
  - share of runs that start a new session (`session_kind`);
  - share of runs that did nothing (`did_nothing`).
- **Cache:** cache-write share of the total; cold resumes, meaning a resume more than an hour after the last turn.
- **Model mix:** share of input-equivalent tokens by model and by run purpose (`purpose`: work a task, PR checks,
  comments, keep warm).
- **Guards that must not get worse:** review rounds per PR, QA bounces, and time from Build to merge.

**Data:** `GET /api/v2/bots/{bot}/turns?since=` returns each run's tokens:

- token counts: `input_tokens`, `cached_tokens`, `cache_write_tokens`, `output_tokens`, `prompt_tokens`;
- `model`, `billing`, `est_cost_usd`;
- `session_kind`, `session_why`, `did_nothing`, `purpose`, `trigger_kind`, `task_id`.

It returns only each bot's newest 200 rows after `since` (there is no `until` or `before`), so it cannot be paged
back by `since`. Busy bots fill 200 rows in about 18 hours, so the Performance Owner collects turns three times a day
and the weekly review reads that store. A bot sees about half the turns that the baseline below counts, so post-change
reads are compared with a same-source baseline, not with that table. Count merged PRs with
`gh search prs --repo <repo> --merged-at <from>..<to>` or the search API. Compare the same hours of the day across
days, since night load is much lighter, and use at least three days for per-PR numbers. Codex runs report no
`prompt_tokens`.

## Baseline: Nov 12–13, 2026, before the changes

24 hours on Tico Team (Opus for almost everything):

| | |
|---|---|
| Runs | 1,537 |
| Input-equivalent tokens per hour | 22.2M |
| Estimated cost (Gateway prices) | about $50 per hour |
| Engineering-bot tokens per merged PR (114 merged) | 3.1M |
| Cost split | cache reads 58%, cache writes 25%, output 13%, fresh input 4% |
| Context per call | median 100k for all bots; Builders median 244k, p90 406k, max 584k |
| Model calls per run | median 12.8, p90 34 |
| New sessions | 59% of runs (670 none saved, 232 fallback side runs) |
| Resumes over an hour idle | 160 runs, 8% of spend in cache writes |
| Did nothing | 13% of runs, about 4% of spend |
| Costliest 10% of tasks | 45% of spend (median 2 runs per task, p90 11) |

## What was wrong

No single cause; about a dozen small ones:

1. **Context never compacted on Claude.** Tico's compaction setting reached Codex but not Claude Code, so sessions grew
   to 400–580k tokens and every call re-read all of it. Context × calls per run is most of the bill.
2. **Ritual reads every run.** Instructions said to read company.md, learnings, standards, patterns and the playbook on
   every run, and to write decisions and patterns at the end of every run. On a resumed session each read re-paid the
   whole context.
3. **Every task started a cold session**, and pipeline turns never forked or reused a warm one.
4. **Cold resumes after reviews.** A review that took over an hour left the Builder's cache expired.
5. **Several bots each loaded the same PR**: the Builder, both Reviewers and the Engineering Manager.
6. **Opus for everything**, including routing, comments and PR-check triage.
7. **Wakes with nothing to do:**
   - every passed check woke the owner;
   - a 120-second "stopped early" re-wake fired after every run.
8. **Fallback side sessions were thrown away**, so each fallback turn started cold.
9. **A new skills folder every turn** (`--add-dir …/skill-sessions/<attempt>`).
10. **The full Tico tool list** (455 tools, about 95k tokens) went to every turn.
    - Claude Code on a subscription and Codex load tools on demand.
    - Gateway (pi) turns appear to send them all.

## What changed

Shipped in v0.6.0-rc.38 (Nov 14, 2026). The rules for every change: settings drive behaviour and no number is
hard-coded; delete our own machinery before adding more; when unsure, run the model.

| # | Change | Where |
|---|---|---|
| 1 | **Claude compacts at Tico's limit:** `compact_at` is passed as `CLAUDE_CODE_AUTO_COMPACT_WINDOW`. Defaults: Opus and Sonnet 300k, Haiku 150k, set per model in Settings. Claude Code's own "auto" (about 967K on 1M models) is deliberately not used. | #3732, #3730 |
| 2 | **Read once per session:** standard files are read when a session starts or after a compaction. Decisions and patterns are written once per task when it leaves the bot's stage. "Read each before you act" was removed from wakes. | #3731 |
| 3 | **Cache breakers:** one skills folder per bot and skill set; task-model fallback sessions are saved and resumed. On Claude Code 2.1.325 a resumed session reuses its first system prompt, so git status, date and added folders do not break the cache. | #3732 |
| 4 | **Skip runs that need no model:** the 120 s re-wake is deleted (the first look comes after 15 min, and cut-off runs still retry after 2 min); a passed check the bot already saw on an unchanged PR head is a note, not a run. Failures, comments, people and stage moves always run. | #3736 |
| 5 | **Developer tool list:** code bots see 35 Tico tools (about 11.6k tokens), not 456. Other tools still work if called. | #3730 |
| 6 | **Haiku subagents line** in code bots' AGENTS.md: Haiku 5.5 extra-high for simple work, Opus for complex or UI work. | #3731 |
| 7 | **Bot model and task model:** heavy roles run their own turns on Haiku 5.5 xhigh and their tasks on Opus 5.5 medium (with ChatGPT only: terra medium and sol high). The Assistant stays on Opus, because Haiku halves its eval score. Hand-set bots keep their settings. | #3730 |
| 8 | **Reviews start from the diff**, the spec and failing checks. Review 2 always runs and effort is unchanged. | #3731 |
| 9 | **Keep warm:** a pipeline setting (per-stage override, default 8 h, 0 is off). An idle task session whose bot still owns a current or later stage gets a hidden one-line ping every 45–55 min, which holds its cache. The ping is skipped when no session is saved or the model or plan changed. | #3736 |

**Considered and not done:**

- **Routing new tasks into warm sessions or forks:** a fork carries the parent's whole context into every later call
  and pays only under about 60k.
- **Bigger tasks by policy:** they grow context, which is the main cost.
- **Review 2 only on risky paths, and lower effort:** rejected by Chris.

## Open follow-ups

Tracked as Tico tasks:

- **Keep warm is being removed** (ticoteam/tico #3811, Chris, open Nov 16): its visible "ok" replies buried real
  questions, and about 2.5% of 1,579 pings were followed by real work within the hour. Before that, **b62349cb**
  (Tasks Product Owner) asked to skip stages whose turn is a person's; Chris answered no on Nov 16 (task a7881084).
  Once #3811 ships, check 9 is retired and the 1–8 h resume write becomes the number to watch.
- Done since Nov 14: **37d4ed7b** (Models Owner, Gateway turns load Tico tools on demand), **729e445c** (Performance
  Owner, the usage-limit fallback resumes its side session). Closed: **c99a4b8c** (UI Owner, read-once rule and
  subagents line in custom bots' AGENTS.md).
- **28434211** (Performance Owner): owns this page and the weekly review routine.
- **Cache audit:** measure cache writes on back-to-back warm resumes, including an effort change.

## First post-change read: Nov 14 09:30Z to Nov 15 02:13Z

Same hours of day as a same-source baseline, Nov 13 09:30Z to Nov 14 02:13Z (16.7 h each), from the bot turns feed.
**Not a fair whole-team comparison:** Builder and Builder 2 were paused from Nov 14 08:09Z and Nov 14 was a Saturday,
so only 4 PRs merged (91 in the baseline). Tokens per merged PR waits for a full working week. The rows below leave the
two Builders out of both windows.

| | Before (no Builders) | After (no Builders) |
|---|---|---|
| Runs | 510 | 327 |
| Input-equivalent tokens per hour | 6.7M | 2.6M |
| Input-equivalent tokens per run | 219k | 132k |
| Context per call, median / p90 | 76k / 148k | 58k / 172k |
| Cost split: cache reads / writes / output / fresh | 50% / 28% / 13% / 9% | 50% / 38% / 12% / 0% |
| New sessions | 78% of runs | 39% |
| Did nothing | 12.9% of runs | 0.3% |
| Fallback runs (Codex) | 153 | 1 |
| Costliest 10% of tasks, share of spend | 30% | 67% |
| Runs over the compact limit +10% | 15 (all bots, max 554k) | 0 (max 245k) |

- Holding: compaction, skip-no-model runs, warm resumes (median cache write 14.9k vs 18.8k), fallback.
- Cache-write share rose because reads fell faster; writes per run fell about 20% (62k to 50k).
- **Keep warm is over its cap:** 165 pings, 5.6% of spend. 25 of the 28 pinged sessions had no real run within an hour
  of their last ping (97% of ping spend); 46% of ping spend was on tasks at OK release, waiting on a person. Filed as
  b62349cb.
- Too early: tokens per merged PR, the code-bot tool list, reviews from the diff (Reviewer 1 median 552k per run on 8
  runs).

## Week 1 read: Nov 9–16 (Monday 00:00Z to Monday 00:00Z)

The turns feed holds this week only from Nov 12 (the collector started then; the feed returns each bot's newest 200
turns), and nothing for Nov 2–9, so there is **no week-on-week comparison** yet. The whole-week ratio (0.5M tokens
per merged PR, 698 PRs) is not valid: its PRs span seven days, its tokens four. Fair reads use the post-change
window, Nov 14 02:13Z to Nov 16 00:00Z (45.8 h), against the baseline above.

| | Baseline, Nov 13 09:30Z–Nov 14 02:13Z | After, Nov 14 02:13Z–Nov 16 00:00Z |
|---|---|---|
| Engineering-bot tokens per merged PR | 1.52M (91 PRs) | 1.64M (43 PRs), not fair: see below |
| Input-equivalent tokens per hour / per run | 11.5M / 240k | 4.3M / 181k |
| Est. cost per hour | $24 | $9.7 |
| Cost split: cache reads / writes / output / fresh | 55% / 26% / 13% / 7% | 56% / 31% / 13% / 0% |
| Context per call, median / p90 | 89k / 213k | 66k / 183k |
| Builders' context, median / p90 / max | 115k / 299k / 554k | 133k / 231k / 262k |
| New sessions | 68% of runs | 37% |
| Did nothing | 12.0% of runs, 7.1% of spend | 1.0%, 0.8% |
| Fallback runs | 272 | 1 |
| Runs over compact limit +10% | 15 | 0 (max 265k) |
| Keep-warm pings | none | 472, 2.9% of spend |
| Model mix | Opus 82%, Codex and Gateway 18% | Opus 100% |

- **Not fair yet:** Builder and Builder 2 have had no turns since Nov 14 (paused 08:09Z) and it was a weekend.
  Merged PRs per day fell from about 150 (Nov 11–13) to 40, 14 and 13 (Nov 14–16).
- **Holding:** compaction, skip-no-model runs, fallback (Codex gone), new-session share.
- **Watch:** warm resumes within the hour write a median 20.2k vs 18.8k (n=157); Reviewer runs average 473k
  vs 294k, context median 107k vs 97k, on a weekend's few reviews.
- **Keep warm** stayed under 5% over the window but went to 5.9% in the 24 h to Nov 16 15:00Z, and its 1–8 h resumes
  wrote a median 52k vs 100k before. It is being removed (#3811).
- Costliest tasks after the change are long Product tasks run pitch to production in one session; on several, about
  half of the runs were keep-warm pings (for example 33 of 66 runs on one task).
- Guard, open-to-merge time of merged PRs (ticoteam/tico): median 1.3 h, p90 26.7 h this week vs 0.7 h and 11.2 h the
  week before, with Builders paused for the last two days. Review rounds and QA bounces are not in the turns feed.

## Weekly review (Performance Owner)

Once a week:

1. Pull seven days of turns for every bot and compute the numbers under "Measuring". Compare with the previous week
   and with the baseline above.
2. List the ten costliest tasks and the five costliest bots, and look for loops. A loop is a task with more than 8 runs,
   repeated PR-check wakes, or repeated fallbacks.
3. Check that the changes above still hold:
   - no Claude context above its model's `compact_at` plus about 10%;
   - heavy bots' own turns on their bot model;
   - keep-warm pings under 5% of spend;
   - did-nothing runs under 5%.
4. File each clear opportunity as a task with its numbers, and update this page: the baseline table if it moved, what
   changed, and the open follow-ups.
