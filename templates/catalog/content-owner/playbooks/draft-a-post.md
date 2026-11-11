# Draft a post

How to make the real piece. Used in three places: the "make the real thing" step of a Building task
(`playbooks/build-a-bet.md`), the next piece of a kept series (upkeep, from Watch), and a writing sub-task another
Owner filed to you. Budget 45 minutes. The outcome is one finished draft in `reports/`, its short versions, and the
draft attached to its task.

Nothing goes live from this playbook. A bet goes live only after a person OKs its Go-live page
(`playbooks/run-and-measure.md`). The next piece of a kept series goes out as the upkeep list in
`knowledge/channel-playbook.md` says. A sub-task's draft goes back to the Owner who filed it.

Inputs: the task, `knowledge/voice.md`, `knowledge/company.md`, `knowledge/series.md` and the last two pieces.

Done when: the draft and its short versions are in the folder, every claim has its source in the notes, the claim check
and any detector score are recorded beside the draft, and the draft is on its task.

---

## 1. Read the request

    tico task show <id>

Know which of the three it is.

- **A bet in Building.** The idea says what to write and why. If its source no longer holds, `playbooks/build-a-bet.md`
  says how to close it.
- **The next piece of a kept series.** Read the series in `knowledge/series.md`: the plan and voice a person OK'd, and
  the pieces already out. The piece stays inside that plan. A piece that needs a new topic, a new kind or a new channel
  is an idea (`playbooks/find-ideas.md`), not upkeep.
- **A writing sub-task from another Owner.** They chose the piece and own the bet. Write it. If it repeats a piece
  already published, say so on the sub-task and link it.

## 2. Read before you write

- `knowledge/voice.md`, always, before the first sentence.
- `knowledge/company.md`, for what is true about the team and what it must never claim.
- The last two pieces in `reports/`, so this one does not repeat them.
- Whatever the idea's source actually said, in full. A summary of a summary produces a draft that
  says nothing.

## 3. Write the piece

One draft, in `reports/YYYY-MM-DD-<slug>/post.md`:

- open with the concrete thing: the situation the reader recognises, or the result, not a wind up;
- make one point, supported, and stop;
- end with one next step for the reader, the one the KPI counts;
- every claim carries its source in a notes section at the end, so a human can check it in a
  minute;
- no number, quote, testimonial, or result you did not read in a dated source;
- no guarantee, no comparison you cannot source, no promise about an outcome.

Then the short versions the team's channels need, in the same folder, one file each. They are
cuts of this piece, not new pieces (`skills/cut-a-post-into-channel-versions/SKILL.md`).

## 4. Check it before you hand it over

Read it once as a reader who has never heard of {{company_name}}: does it say something true and
useful, or does it say that the team exists? Read it once against `knowledge/voice.md`: does it
sound like the team or like a machine? Rewrite the generic sentences and add the missing fact. If it still reads as a
machine after two rewrites, stop. Keep it as internal work and say so. Do not ask a human to rewrite it for you. If the
team runs an AI-text detector, follow `skills/handle-a-detector-score/SKILL.md`. Run
`skills/check-a-claim-before-it-ships/SKILL.md` on every piece, whatever the detector says. Run `tico brand check -` on
the piece and each short version, and fix every hit.

## 5. Hand it over

Commit, then attach the draft to its task.

- **A bet in Building:** go back to `playbooks/build-a-bet.md` for the Design Owner's brand check and the Go-live page.
  The Go-live page shows the piece as it will look.
- **The next piece of a kept series:** where a granted Tool lets you, publish it on its date and record when. Where
  none does, file it for the person who puts pieces live, with the piece attached:

      tico task create --owner <your manager> --label channel:content --title "Apply: publish <piece> on <date>" --body-file <body file>

  The first owner is your Manager, the bot your line in `tico team show` reports to: it checks the task once and
  passes it to the person. A task filed straight to the person is refused.

  The body file's first paragraph is one or two plain sentences, under 40 words, with no links, ids or file paths.
  Below it, after a blank line: where it goes, the date, and the path to the draft and its short versions. If it is
  refused, nothing was filed: fix it and run it again.

- **A writing sub-task:** `tico task done <id> -m "..." --version N`: what the piece says in one line, the path to the
  folder, and what is still missing.

## 6. Keep the series honest

In the same run, update `knowledge/series.md`: what this piece is, and when it goes out. A reader question this run
turned up goes in `knowledge/questions.md`, with where it came from.

## When an OK lapses

A Go-live page with no decision is neither a yes nor a no. Nothing is posted. Keep the drafts as reusable pieces, do not
post them anyway, and ask once more on the task. A closed task is not an OK: resolved is not a ship yes.

## When the clock runs out

Finish the draft and cut the short versions, not the other way round. Say in the note which short
versions are missing. An outline is not a draft and is never reported as one.
