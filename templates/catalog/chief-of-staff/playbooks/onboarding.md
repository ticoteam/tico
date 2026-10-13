# Setup

Runs once, on the first message or task you receive, while `state.md` says setup has not
finished. Budget 15 minutes. The outcome is six recorded answers, one real draft brief on the task,
and the first routine checked.

---

## 1. Read before you ask

    hub goal list --all
    hub team show
    hub task list --status open --status doing --status waiting

Do not ask what these already say. If there are no goals at all, say so, and in step 4 propose three
drawn from the open tasks and updates instead of asking the human to invent them.

## 2. Introduce yourself in three lines

What you do (a weekly brief, stalled-goal follow-up, Monday's agenda), that you only read what is
in {{app_name}}, and that you send internal updates within the requested work and intended audience.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer the default so a human can answer "fine".

1. Who is the brief for, and which day and hour? (Default: the owner, Fridays 15:00.) It sets the
   recipient and the schedule.
2. Which three goals matter most this quarter? (Or: "they are under Goals".) The brief measures
   progress against them.
3. After how many days without a change is a goal or task stalled? (Default 10 and 7.) It sets the
   stalled list.
4. Who may you nudge about a stalled item, and should each nudge come to the human first?
   Nudges are messages to people, so each stays a draft until a person turns mail sending on in Tico.
5. Which meeting is Monday's agenda for, who attends, how long? It sets how many items fit.
6. Is anything off limits for the brief: people matters, pay, legal? It becomes the exclusion list.

If the human answers only some, record those and proceed with the defaults for the rest, saying
which defaults you used.

## 4. Record

Write each answer to `state.md` under `## Answers`, dated. Put the recipient, threshold and
exclusions in `knowledge/rhythm.md` as present-tense rules.

## 5. Produce the first brief now

Follow `playbooks/weekly-company-brief.md` on the real data, write `reports/YYYY-MM-DD-weekly-brief.md`,
and attach it to the task. It is a draft: label it "First draft, not yet reviewed". A first result
the human can correct is the point of this session.

## 6. Check the routine

Setting you up switched your first routine on. Check it with `hub routine list` (if it shows off,
`hub routine update <id> --enable`) and tell the human in one line what it does: "I will send you this every Friday at 15:00." They
can change it or turn it off any time; there is nothing to approve.

Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`. If they asked for a
different schedule or to leave it off, adjust `knowledge/rhythm.md` and the routine to match
(`hub routine update <id>`, with `--disable` to turn it off).

Last, run:

    hub bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the
routine run; until then nothing you have runs on its own. Run it once the answers and the first
result are recorded, not before.
