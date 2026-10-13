# Draft a post

Triggered by a task that names one piece, or by an idea handed over by another bot that you have
decided is worth writing. Budget 45 minutes. The outcome is one finished draft in `reports/`, its
short versions, and a task note with the result and destination.

---

## 1. Decide it is worth writing

    hub task show <id>

An idea from another bot is an idea, not an assignment. It is worth a piece when it answers a
question a real reader has, it is something {{company_name}} can say truthfully and specifically,
and it is not what the last two pieces already said. If it fails any of those, say so in one line
on the task and add it to `knowledge/ideas.md` for later rather than writing it thinly.

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
- every claim carries its source in a notes section at the end, so a human can check it in a
  minute;
- no number, quote, testimonial, or result you did not read in a dated source;
- no guarantee, no comparison you cannot source, no promise about an outcome.

Then the short versions the team's channels need, in the same folder, one file each. They are
cuts of this piece, not new pieces.

## 4. Check it before you hand it over

Read it once as a reader who has never heard of {{company_name}}: does it say something true and
useful, or does it say that the team exists? Read it once against `knowledge/voice.md`: does it
sound like the team or like a machine? If the second read fails twice in a row after rewriting,
stop. Keep it as internal work and say so. Do not ask a human to rewrite it for you.

## 5. Hand it over

Commit, then `hub task update <id> --status done --note`: what the piece says in one line, the path
to the folder, what is still missing, and one line on what you did and any missing Tools. Publish or schedule requested content at the named destination with your Tools when a person has turned mail sending on in Tico; otherwise keep the draft.

## 6. Keep the plan honest

In the same run, update `knowledge/plan.md`: this piece moves from planned to drafted, and anything
this run taught you about what to write next goes in `knowledge/ideas.md` with where it came from.

## When the clock runs out

Finish the draft and cut the short versions, not the other way round. Say in the note which short
versions are missing. An outline is not a draft and is never reported as one.
