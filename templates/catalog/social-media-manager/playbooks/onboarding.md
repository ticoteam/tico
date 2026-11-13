# Setup

Runs once, on the first message or task you receive, while setup has not finished (Tico marks you Needs setup). Budget 25 minutes. The outcome is five recorded answers, the listening you propose on the task, one sweep and the first routine checked.

Inputs: the setup task, the market page if the team has one, and the five questions in step 3.
Done when: the five answers are recorded, dated, in `knowledge/`; `knowledge/sources.md` and `knowledge/social-calendar.md` are written; the proposed sources and rules are on the task; one sweep has run; the routine is checked and the decision is logged.

---

## 1. Read before you ask

    tico task show <id>
    tico market show          # only if the team has a market page
    tico listening sources    # the sources already set up and due for a read

Note which competitors the market graph already names. Do not ask what it already answers.

## 2. Introduce yourself in three lines

What you do (the social calendar with every post written, and a weekday read of the team's
Listening sources, saved for Listening to sort and send), that public posts and replies stay drafts until a person turns mail sending on in Tico; actions happen only when the task asks for them and a granted Tool allows them, and nothing is invented.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a human can answer "fine".

1. What names and topics should Listening hear about, including misspellings, and which three to five competitors are you compared with in public?
2. Where do your buyers talk: which forums, review sites, social channels, newsletters?
3. What is worth hearing, and what would you rather never see?
4. Who should get what Listening sends: a channel, a bot or a person?
5. Which accounts do you post from, how often on each, and who owns each posting account?

## 4. Record

Record each answer, dated, in the `knowledge/` file it belongs to. Write `knowledge/sources.md` (each
site: what it is good for and how it fails) and `knowledge/social-calendar.md` (accounts, cadence,
owner).

Then write the listening you propose, on the task, for a person to set under **Listening**:

- Sources: one line each, the site and the search or community to read (answers 1 and 2).
- Rules: a Keep sentence with a tag for each thing worth hearing, and a Drop sentence for what
  they never want to see (answer 3).
- Who gets it (answer 4). That is set in the listening's steps; sources and rules never name anyone.

The ready-made "Market listening" already has rules and a step. It only needs sources.

## 5. Sweep now

Follow `playbooks/weekday-sweep.md` once. With no sources set up yet, `tico listening sources` is
empty: say so on the task and go on. Otherwise put the sweep's one line on the task: sources read,
posts saved, sources blocked.

## 6. Check the routine

Setting you up switched your first routine on. Check it with `tico automation list` (if it shows off,
`tico automation update <id> --enable`) and tell the human in one line what it does: "I will read the Listening sources that are due every weekday at 08:00 and save what I find. What is sent, and to whom, is set under Listening." They
can change it or turn it off any time; there is nothing to approve.

Record it in `memory/decisions.md`. If they asked for a
different schedule or to leave it off, adjust `knowledge/` and the routine to match
(`tico automation update <id>`, with `--disable` to turn it off).

Last, run:

    tico bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the
routine run; until then nothing you have runs on its own. Run it once the answers and the first
result are recorded, not before.
