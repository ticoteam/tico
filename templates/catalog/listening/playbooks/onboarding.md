# Setup

Runs once, on the first message or task you receive, while `state.md` says setup has not
finished. Budget 25 minutes. The outcome is five recorded answers, one real digest on the task and the first routine checked.

Inputs: the setup task, the market page if the team has one, and the five questions in step 3.
Done when: `state.md` holds five dated answers; `knowledge/watchlist.md`, `knowledge/sources.md` and `knowledge/social-calendar.md` are written; the first digest is on the task labelled as a draft; the routine is checked and the decision is logged.

---

## 1. Read before you ask

    hub task show <id>
    hub market show          # only if the team has a market page

Note which competitors the market graph already names. Do not ask what it already answers.

## 2. Introduce yourself in three lines

What you do (the social calendar with every post written, and a short digest of public mentions,
questions and competitor moves), that public posts and replies stay drafts until a person turns mail sending on in Tico; actions happen only when the task asks for them and a granted Tool allows them, and nothing is invented.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a human can answer "fine".

1. What names should I listen for, including misspellings, and which three to five competitors are you compared with in public?
2. Where do your buyers talk: which forums, review sites, social channels, newsletters?
3. What would you want to know the same day, and what would you rather never see?
4. Who receives findings, and should anything reach them other than the digest?
5. Which accounts do you post from, how often on each, and who owns each posting account?

## 4. Record

Write each answer to `state.md` under `## Answers`, dated. Write `knowledge/watchlist.md` (names,
queries with exclusions, sources), `knowledge/sources.md` and `knowledge/social-calendar.md`
(accounts, cadence, owner).

## 5. Sweep now

Follow `playbooks/weekday-sweep.md` once. Write the digest in the shape of
`knowledge/examples/sweep-digest.md` and attach it to the task, labelled "First draft, not yet
reviewed". Create no child tasks yet: list what you would create.

## 6. Check the routine

Setting you up switched your first routine on. Check it with `hub automation list` (if it shows off,
`hub automation update <id> --enable`) and tell the human in one line what it does: "I will sweep every weekday at 08:00 and send you one short digest. Quiet days are one line." They
can change it or turn it off any time; there is nothing to approve.

Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`. If they asked for a
different schedule or to leave it off, adjust `knowledge/` and the routine to match
(`hub automation update <id>`, with `--disable` to turn it off).

Last, run:

    hub bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the
routine run; until then nothing you have runs on its own. Run it once the answers and the first
result are recorded, not before.
