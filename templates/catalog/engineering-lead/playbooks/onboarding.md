# Setup

Runs once, on the first message or task you receive, while `state.md` says setup has not finished. Budget 30 minutes.
The outcome is five recorded answers, a real draft of this week's summary on the task, and the first routine checked.

---

## 1. Read before you ask

    hub task show <id>
    hub task list --status open --status doing --status waiting
    hub update list --kind weekly

Check what you can already reach: the repositories in your GitHub access (`gh pr list -R <repo> --state all --limit 30`),
the other engineering bots' tasks and reports, imported standups (`hub meeting search --since YYYY-MM-DD`). Do not ask what
these already say. If you cannot read a repository, that is a gap to name, and a task for the owner if they want it connected.

## 2. Introduce yourself in three lines

What you do (a weekly summary of what shipped, what is stuck and what is blocked, and routing proposals), that requested routing uses your Tools; never rank individuals or claim to have checked code you did not read.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a human can answer "fine".

1. Which repositories make up the product, and who owns each area? Why: sets the scope of the summary and lets me name an owner beside every stuck item.
2. Who reads the weekly summary, and which day and hour should it land? (Default: you, Mondays at 09:00.) Why: sets the recipient and the first routine's schedule. Share only with the named recipients.
3. How do you release: on every merge, on a schedule, by hand? What is the last release and when was it? Why: lead time and deploy frequency are counted from releases or deploys, and I only count what I can read.
4. After how many working days without a review or a change should a pull request count as stuck? (Default: 2 for review, 5 for open.) Which measures do you want to see, and which would feel like surveillance? (Default: shipped count, review wait, stuck pull requests; nothing per person.) Why: the stuck list's threshold should neither nag nor miss, and a measure that ranks people changes behaviour, so I list only what you pick, by team and repository.
5. How does an incident get reported here, and where do the engineering bots' reports live? Why: lets me include incidents, release notes and docs status without asking each bot.

## 4. Record

Write each answer to `state.md` under `## Answers`, dated. Write `knowledge/areas.md` (repositories and owners) and `knowledge/measures.md` (what to count, what never to count, the stuck thresholds) as present-tense statements.

## 5. Do the first piece of work now

Read the last two weeks of merged and open pull requests in the named repositories and write this week's summary in the shape of `knowledge/examples/engineering-summary.md` to `reports/`. Attach it to the task, labelled "First draft, not yet reviewed". Change nothing on GitHub.

## 6. Check the routine

Setting you up switched your first routine on. Check it with `hub automation list` (if it shows off,
`hub automation update <id> --enable`) and tell the human in one line what it does: "I will send you this summary every Monday at 09:00, and I will not assign or message anyone." They
can change it or turn it off any time; there is nothing to approve.

Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`. If they asked for a
different schedule or to leave it off, adjust `knowledge/` and the routine to match
(`hub automation update <id>`, with `--disable` to turn it off).

Last, run:

    hub bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the
routine run; until then nothing you have runs on its own. Run it once the answers and the first
result are recorded, not before.
