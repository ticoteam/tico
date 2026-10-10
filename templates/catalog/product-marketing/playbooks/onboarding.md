# Setup

Runs once, on the first message or task you receive, while `state.md` says setup has not
finished. Budget 30 minutes. The outcome is five recorded answers, a first launch brief on the task and
the first routine checked.

---

## 1. Read before you ask

    hub task show <id>
    hub market show
    hub goal list --all

Read the team's public product page and what the market graph says about competitors. Do not
ask what these already say. If there is no market page, that is part of answer two.

## 2. Introduce yourself in three lines

What you do (launch briefs, positioning, battlecard drafts, a weekly launch review), that requested launch actions use your Tools, public messages stay drafts until a person turns mail sending on in Tico, and claims need sources.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a human can answer "fine".

1. What do you sell, to whom, and what would that customer do if you did not exist?
2. Which three to five competitors do you meet in deals, and where do you win and lose?
3. What is launching in the next 90 days, and how big is each one?
4. Who signs off launch messaging, and who must be told first?
5. Can you share the current pitch, the product page and one recent win and one loss?

## 4. Record

Write each answer to `state.md` under `## Answers`, dated. Write `knowledge/positioning.md`,
`knowledge/launches.md` and `knowledge/tiers.md` (default tiers as in AGENT.md unless they differ).

## 5. Draft now

Take the next launch and follow `playbooks/launch-brief.md`. Write it in the shape of
`knowledge/examples/launch-brief.md`, attach it to the task, labelled "First draft, not yet
reviewed". Nothing is published.

## 6. Check the routine

Setting you up switched your first routine on. Check it with `hub routine list` (if it shows off,
`hub routine update <id> --enable`) and tell the human in one line what it does: "I will review your launches and positioning every Monday at 10:00 and hand you drafts." They
can change it or turn it off any time; there is nothing to approve.

Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`. If they asked for a
different schedule or to leave it off, adjust `knowledge/` and the routine to match
(`hub routine update <id>`, with `--disable` to turn it off).

Last, run:

    hub bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the
routine run; until then nothing you have runs on its own. Run it once the answers and the first
result are recorded, not before.
