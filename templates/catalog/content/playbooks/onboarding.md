# Setup

Runs once, on the first message or task you receive, while `state.md` says setup has not
finished. Budget 30 minutes. The outcome is five recorded answers, one real draft on the task and the first routine checked.

---

## 1. Read before you ask

    hub task show <id>

Read what the team already published if a link is given, and any voice or style notes attached.
Do not ask what these already say.

## 2. Introduce yourself in three lines

What you do (a rolling content plan and finished drafts with short versions), that requested publication uses your Tools when a person has turned mail sending on in Tico; otherwise the work stays a draft.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a human can answer "fine".

1. Who reads what you publish, and what should they do after reading it?
2. Where do you publish, how often, and who presses publish?
3. Can you paste or link two pieces you are proud of and one that missed?
4. What do customers keep asking, and which three topics could you talk about for an hour?
5. What must never appear in public?

## 4. Record

Write each answer to `state.md` under `## Answers`, dated. Write `knowledge/voice.md` (with the two
good examples), `knowledge/plan.md` (four weeks, seeded from answer four) and the never-say rules
as present-tense statements.

## 5. Draft now

Take the top idea and follow `playbooks/draft-a-post.md`. Attach the draft and its short versions to
the task, labelled "First draft, not yet reviewed", in the shape of
`knowledge/examples/content-plan.md`. Nothing is published.

## 6. Check the routine

Setting you up switched your first routine on. Check it with `hub routine list` (if it shows off,
`hub routine update <id> --enable`) and tell the human in one line what it does: "I will send you the next four weeks and one finished draft every Monday at 09:00, and a human publishes." They
can change it or turn it off any time; there is nothing to approve.

Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`. If they asked for a
different schedule or to leave it off, adjust `knowledge/` and the routine to match
(`hub routine update <id>`, with `--disable` to turn it off).

Last, run:

    hub bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the
routine run; until then nothing you have runs on its own. Run it once the answers and the first
result are recorded, not before.
