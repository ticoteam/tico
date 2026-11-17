# Find ideas

Routine `find-ideas`, weekly. Also run when a task asks. Budget 30 minutes.

Outcome: at most 3 new ideas in Growth Experiments' Ideas stage, each one line (what to try and why) with your channel
tag, none already in the pipeline; and, when Building has room, your best idea started. This routine's own task is
then done.

The same text is shipped to every channel Owner. Where your ideas come from is in `knowledge/channel-playbook.md`
("Where ideas come from"). Read it first.

---

## 1. Read where things stand

    tico task show <id>
    tico pipeline show growth-experiments
    tico task list --pipeline growth-experiments --label <your channel tag> --status open --brief

Note what is already in Ideas, Building and Running for your channel, and how many tasks Building holds.

## 2. Look for ideas

Read the sources "Where ideas come from" names: your channel's own numbers, the Monitoring tasks closed or slipping
this month, and the company's competitor notes:

    tico market delta
    tico market find "<a competitor>"

Read How we sell in `tico strategy show`. An idea is worth filing when it would move your KPI, a source shows why, and
your channel can try it in a few weeks.

## 3. Skip what is already there

Before you file one, search the pipeline for it, open and finished:

    tico task list --pipeline growth-experiments --q "<two or three key words>" --status open --status done --status closed --brief

The same idea open, or tried in the last 90 days, is not filed again. An idea tried before and failed is filed again only
when something has changed, and the line says what.

## 4. File at most 3

    tico task create --owner me --pipeline growth-experiments --label <your channel tag> --title "<what to try>" --body "<why, in one line, with the source and its date>"

A new task lands in Ideas. Three is the most per run, not a goal: one good idea beats three weak ones, and none is fine.
An idea whose number belongs to another channel goes to that channel's Owner as theirs (`--owner <their slug>`, their
tag).

## 5. Start one when Building has room

Ideas never move by themselves. Pick your idea with the best evidence and start it:

    tico task move <idea id> Building -m "<why this one now>" --version N

When Building has no room, the move says so: leave the idea where it is and go on. Building is where a person OKs what
goes live, so it holds 6 at most. Work the idea you started in its own run (`playbooks/build-a-bet.md`).

## 6. Close

    tico task done <id> -m "..." --version N

The note: the ideas filed (their numbers), the ones skipped as already there, the one started if any, and what you
could not read.
