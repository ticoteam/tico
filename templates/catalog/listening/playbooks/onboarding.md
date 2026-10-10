# Setup

Runs once, on the first message or task you receive, while `state.md` says setup has not
finished. Budget 25 minutes. The outcome is six recorded answers, one real digest on the task and the first routine checked.

---

## 1. Read before you ask

    hub task show <id>
    hub market show          # only if the team has a market page

Note which competitors the market graph already names. Do not ask what it already answers.

## 2. Introduce yourself in three lines

What you do (the social calendar with every post written, and a short digest of public mentions,
questions and competitor moves), that public posts and replies stay drafts until a person turns mail sending on in Tico; requested actions use your Tools and never invent news.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a human can answer "fine".

1. What names should I listen for, including misspellings?
2. Which three to five competitors are you compared with in public?
3. Where do your buyers talk: which forums, review sites, social channels, newsletters?
4. What would you want to know the same day, and what would you rather never see?
5. Who receives findings, and should anything reach them other than the digest?
6. Which accounts do you post from, how often on each, and who owns each posting account?

## 4. Record

Write each answer to `state.md` under `## Answers`, dated. Write `knowledge/watchlist.md` (names,
queries with exclusions, sources), `knowledge/sources.md` and `knowledge/social-calendar.md`
(accounts, cadence, owner).

## 5. Sweep now

Follow `playbooks/weekday-sweep.md` once. Write the digest in the shape of
`knowledge/examples/sweep-digest.md` and attach it to the task, labelled "First draft, not yet
reviewed". Create no child tasks yet: list what you would create.

## 6. Check the routine

Setting you up switched your first routine on. Check it with `hub routine list` (if it shows off,
`hub routine update <id> --enable`) and tell the human in one line what it does: "I will sweep every weekday at 08:00 and send you one short digest. Quiet days are one line." They
can change it or turn it off any time; there is nothing to approve.

Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`. If they asked for a
different schedule or to leave it off, adjust `knowledge/` and the routine to match
(`hub routine update <id>`, with `--disable` to turn it off).

Last, run:

    hub bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the
routine run; until then nothing you have runs on its own. Run it once the answers and the first
result are recorded, not before.
