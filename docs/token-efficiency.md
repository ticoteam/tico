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

It returns at most 200 rows per call, so page by `since`. Count merged PRs with
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

- **c99a4b8c** (Models Owner): Gateway (pi) turns should load Tico tools on demand.
- **37d4ed7b** (Performance Owner): the usage-limit fallback inside a turn should resume its side session.
- **729e445c** (UI Owner): custom bots (UI Owner, Code Hygiene Owner) need the read-once rule and the subagents line in
  their own AGENTS.md.
- **Keep-warm pings on Codex** rely on wording alone, because Codex has no per-turn limit.
- **Cache audit:** measure cache writes on back-to-back warm resumes, including an effort change.

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
