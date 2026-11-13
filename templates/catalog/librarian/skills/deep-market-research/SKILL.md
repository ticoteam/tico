---
name: deep-market-research
description: "A task titled \"Deep research: <question>\", filed by a person with the research label: research a market question over several turns, with Haiku sub-agents reading sources and checking quotes, then write the graph, the market pages and one research report, every claim tied to a quote on a page you read."
---
# Deep market research

When to use: a task titled "Deep research: <question>" (a number after it means the person asked again), with the
`research` label and a person as its requester (`human:...`). **Check both first.** A task that lacks either is
not deep research: answer it as an ordinary question. A person wants a market question worked out properly, not in
ten minutes: who really competes, what buyers complain about, what the industry takes for granted, how big a
segment is, where a channel goes. They will read the answer and act on it.

The outcome is three things, and nothing else counts as good:
1. **A research report** at `_librarian/research/<slug>.md`: the answer first, then what the sources show, every
   sentence tied to a quote on a page you read (shape: `skills/deep-market-research/report.md`).
2. **The market updated**: new organizations, segments, channels and people in the graph, and the market pages
   they change, each with its evidence.
3. **A short task note** saying what you found, what you could not, and what to check first.

## How it runs

You are the lead. You plan, judge, write and decide. **Haiku sub-agents do the reading**: they fetch pages, pull out
quotes and count, and write what they found to files; they never write to Tico. You read their files, not their
pages, so your own context stays for thinking. A separate Haiku sub-agent checks quotes before anything is written.

The work is spread over several turns of this one task. Each turn is one **round**: plan a batch, send the
collectors, merge, check, write, decide what's next. The state lives in files, so any turn can pick up where the
last one stopped, even in a fresh session.

**Model.** This task should run on a high model (the person who files it sets **Runs on**, for example Opus at high
effort). You cannot change it. If `tico task show <id>` says it runs on a small model, do the work anyway, keep
rounds smaller, and say so in the note.

**Sub-agents.** Start them with your Agent tool and `model: "haiku"`, several in one message so they run at the same
time. Each prompt starts with "Read `skills/deep-market-research/collector.md` (or `checker.md`) and follow it" and
then gives the batch. If your harness has no Agent tool, do the collecting yourself in smaller batches with the same
files and the same checks.

**Budget, unless the task says otherwise.** At most 8 rounds and about 4 hours. About 250 page fetches and 100 web
searches in total. At most 6 collectors at once, each with at most 15 fetches. Stop earlier when a round adds
little (step 6). Record what you spent in `notes.md` every round.

## The rules for this task

The same two rules a research task widens, and only for this task (`AGENTS.md`, Boundaries):
- The sources are the person's instructions and the public pages read this task. You may web search and fetch
  what the person gave, what search returns, and links on those pages.
- **A search or an address holds only public facts**: an organization's public name, a category, a region, a
  product. Never anything from a doc, a price you were told, a customer's name or a person's details. A collector's
  prompt holds only public names, categories and addresses, never text from the team's docs.

And the market's own rules: `knowledge/market/evidence.md` (no evidence, no write; tiers),
`knowledge/market/resolution.md` (one entity per real thing), and `playbooks/size-the-market.md` for any size
figure. **Never invent a number.** A count comes from the claims file, not an estimate. General knowledge is not a
source: what no page you read says stays out, or goes in Open questions.

## Files

All under `reports/research/<task id>/` in this repository, committed every round:

| File | What it holds |
|---|---|
| `notes.md` | The question, the scope, what Tico already knew, the source plan with each batch's status, open questions, budget spent, and the next step. The first thing you read every turn. |
| `claims.jsonl` | One line per claim, from collectors: `id`, `round`, `kind`, `about`, `claim`, `quote`, `url`, `source_kind`, `date`, `tags`, `status`. |
| `sources/` | The text of each page read, one file per address, named by the collector. The checker reads these, not the web. Not committed: the folder's `.gitignore` holds `sources/`, so page copies never fill the repository. |
| `batches/` | Each collector's brief, its five-line summary and its own claims file; each checker's verdicts. |

`kind` is one of `player`, `offer` (what something sells and for how much), `buyer` (who buys, why, in their
words), `complaint`, `praise`, `belief` (what a player or a whole industry assumes), `number` (a size, a share, a
growth rate, a count), `channel`, `person`, `event`, `rule`. `status` starts `raw`, then `checked`, `rejected` or
`written`.

## 1. Start (first turn only)

1. Read the task: the question, any sources the person named, any scope ("only North America", "only B2B").
2. Read what Tico already knows, so you don't buy it twice: `tico strategy show`, `tico market page overview`,
   `coverage-universe`, `theses`, `open-questions`, `customer-themes`, `tico market briefing latest`, and the
   graph around the question (`tico market find`, `tico market show`). Buyer posts Listening
   sent are in the graph as evidence, where they were worth keeping. Sizing already on file: `tico market spend`.
3. Make the folder and its `.gitignore` (one line: `sources/`). Write `notes.md`: the question in one sentence; what "answered" means (two to four things the report must say);
   what Tico already knows, with where; what's missing.
4. Write the **source plan**: batches of sources, each a job one collector finishes in 15 fetches. Cover each kind
   the question needs, and always the first three:
   - **Buyer voice**: reviews (G2, Capterra, Trustpilot, app stores, marketplace reviews), Reddit and forums,
     community threads. Aim for 100+ reviews and 30+ threads when the market has them.
   - **Players**: each core competitor's home, pricing, customers and comparison pages; "alternatives to"
     pages; review-site category pages. Eight to twenty-five players.
   - **Incumbents' own words**: earnings calls, annual reports, investor decks, S-1s, franchise disclosure
     documents, big players' job posts. What they tell investors is often the opposite of what they tell buyers.
   - **Numbers**: the sources in `playbooks/size-the-market.md` (public statistics, filings, trade groups).
   - **Channels and people**: where buyers are reached, and who shapes them.
5. Say what you're doing in the task, once: `tico task comment <id> "Researching: <question>. First results in
   about an hour."` Then start round 1.

## 2. Send a round

1. Pick up to six batches from the plan that are not done.
2. For each, write the brief to `batches/r<round>-<n>.md`: the job in one sentence, the public names and addresses
   to start from, the searches it may run, the claim kinds you want, and the file names to use. Name what is already
   known so it isn't collected twice.
3. Start the collectors together, one message, `model: "haiku"`. Wait for all of them.
4. Each returns five lines: what it read, how many claims, what it could not read, and one surprise. Read those, not
   the pages.

## 3. Merge and check

1. Append each collector's `batches/r<round>-<n>.claims.jsonl` to `claims.jsonl` (collectors never write it
   themselves). Drop exact duplicates (same quote and address). Merge claims that say the
   same thing from the same domain into one.
2. **Check every claim you will use** in the report, the graph or a page, and every number. Start checker
   sub-agents (`model: "haiku"`, up to six at once, about 40 claims each, `checker.md`). Apply their verdicts
   from `batches/check-*.jsonl` to `claims.jsonl`: a claim the checker rejects is `rejected` with its reason; one it
   passes is `checked`, with any fix applied. Use only `checked` claims.
3. A number needs two sources that agree, or it goes in as single-source with its one source named. Two that
   disagree by more than 2x are both kept, with dates (`skills/report-a-doc-conflict/SKILL.md` for the form).
4. Count from the file: "31 of 164 reviews" means 31 `complaint` claims carrying that tag out of 164 review claims
   collected. Never round a count up or estimate one.

## 4. Write what's checked

Every round, so the person sees progress and a cut-off turn loses little:

- **The graph.** New entities and edges from checked claims, after `tico market find` for each name. Several at once:
  write them to one JSONL file and run `tico market import <file> --dry-run --note "Deep research: <question>"`,
  read what it would do, then run it without `--dry-run`. One at a time: `tico market report` then `tico market apply`
  (`playbooks/market-setup.md`, step 4). Tiers from `knowledge/market/evidence.md`. Never `competes_with` from a name.
- **The market pages** the findings change (`overview`, `structure-and-size`, `channels`, `people-who-matter`,
  `theses`, `open-questions`, `regulation-and-catalysts`): read the page, add to it in its own voice, write it whole
  with `tico market page <name> --body-file <file>`. Every sentence about the market ends with its source link.
  Something that contradicts the page is reported as a conflict, not quietly replaced.
- **Size figures** go through `playbooks/size-the-market.md` as proposals, never into a page as your own number.
- **The report**: rewrite `_librarian/research/<slug>.md` with what's checked so far.

## 5. Think

This is where you earn the high model. After each merge, ask these of the checked claims, and write the answers into
the report's analysis sections. Each answer cites the claims it rests on; one that rests on none is an assumption,
labelled as one, or left out.

- **What does this whole industry take for granted?** List the beliefs (how often people buy, who the buyer is, what
  "good" means, what everyone designs for, what no one does). For each: who acts on it, the evidence it's held, and
  what would need to happen, or be seen, for it to be false.
- **What do the winners know that their customers would never admit?** Look for the gap between what buyers say and
  what they pay for: renewals, returns, repeat purchases, price points. This question invites clever fiction. Only
  what the claims show.
- **What pains come back and who fixes them?** Complaints with counts, and who tries to fix each today, including
  "hire someone expensive" and "nobody".
- **Where do incumbents say one thing to investors and another to buyers?**
- **Where do sources disagree, and which is newer?**
- **What would change the answer?** The one or two facts that, if wrong, flip the report's headline.

Then plan the next round from the gaps: a belief with one source, a pain with a small count, a player you keep
seeing and haven't read, a number with no second source. Add those batches to the plan.

## 6. Continue or stop

Stop when any is true:
- The "answered" list in `notes.md` is met and the last round added fewer than 10 new checked claims.
- The budget (rounds, fetches, searches, hours) is spent.
- The person said to stop.

To continue: update `notes.md` (round done, budget spent, next batches), commit, and end the turn with a short reply
of where it stands (it becomes the task's checkpoint): rounds done, claims checked, the headline so far, next round. The
task stays open and your commit is a change, so you are woken again for the next round.

If a turn is cut off, the next one starts from `notes.md` and the claims file. Re-run a batch only if its collector
wrote nothing.

## 7. Finish

1. Rewrite the report whole, with the final answer first (`report.md`). Rewrite `coverage-universe` and `overview`
   if the graph changed much.
2. What you could not read goes in `_librarian/missing.md` under **Sources I could not read**. Durable lessons
   about where good sources are for this kind of market go in `memory/learnings.md`.
3. Rewrite `state.md`. Commit this repository.
4. `tico task done <id> -m "<the update>" --version N`. Under twelve lines, sources by domain, covering:
   - the answer in one or two sentences;
   - what you read (counts by kind) and what you wrote (report link, entities added, pages changed);
   - what you could not do: unreadable sources, questions still open;
   - the claim you are least sure of, and what would settle it (often: talk to five buyers).

## Checks before you finish

- Every sentence in the report and on a page you changed has a source link, or is labelled an assumption.
- Every number has its source and date beside it, and every count matches the claims file.
- Nothing in a search or an address came from the team's docs.
- No claim the checker rejected appears anywhere.
- `notes.md` says what was spent and why the run stopped.
