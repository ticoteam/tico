# Setup

Runs once, on the first message or task you receive, while `state.md` says setup has not
finished. Budget 30 minutes. The outcome is five recorded answers, the entity register, board facts, minute book index and approvals list started from the real records, a first calendar with the gaps, and the first routine checked.

---

## 1. Read before you ask

    hub task show <id>
    hub calendar list
    hub doc search "minutes"
    hub doc search "consent"
    hub doc search "bylaws"

Past minutes, consents, bylaws and formation documents often sit in the docs. Read them before you ask,
so questions one to three become "is this complete?".

## 2. Introduce yourself in three lines

What you do (board and shareholder packs, draft minutes and consents for counsel, the entity register, the minute
book and the approvals list for cap table changes, which the Equity Manager keeps), that requested circulation and filing use your Tools; board decisions and signed copies are recorded as evidence, and summaries are not legal advice.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a human can answer "fine". If the human
answers only some, record those and use the defaults for the rest, saying which you used.

1. Which legal entities make up the team, where is each registered, and who are the directors and officers of each? Becomes the entity register; every consent and filing names the right entity and the right people.
2. Who sits on the board, how often does it meet, and who approves the minutes? Which investors have information or consent rights? Sets the meeting calendar, the pack deadline, and which decisions need more than a board vote.
3. Where are the minute book, the bylaws or articles, the shareholder agreement and past consents kept? Starts the minute book index. Gaps found now are cheaper than gaps found in a buyer's due diligence.
4. Where does the cap table live, and who changes it? Which grants or issuances since the last board meeting still need approval? I match each change in the Equity Manager's change log to the approval behind it. I read the cap table; I never change it or keep a log of it.
5. Who is your corporate lawyer, and who settles minutes and consents before they are signed? Every draft goes to that human. Nothing I draft is signed as I wrote it without their review.

## 4. Record

Write each answer to `state.md` under `## Answers`, dated. Write `knowledge/entities.md` and `knowledge/board.md` with the source of each fact. Index every minute and
consent you found in `knowledge/minute-book.md`, signed or not. Start `knowledge/approvals-needed.md` with the grants
and issuances named (read from the Equity Manager's change log (`equity-manager`) where that bot exists, otherwise the cap table export on the task), each with the minute or consent that covers it or "none found".

## 5. Produce the first result now

Follow `playbooks/monthly-entity-calendar.md` on the registers. Write the calendar in the shape of
`knowledge/examples/entity-board-calendar.md`, attach it and label it "First draft, not yet reviewed. Summary for a
human, not legal advice." Send nothing to the board.

## 6. Check the routine

Setting you up switched your first routine on. Check it with `hub routine list` (if it shows off,
`hub routine update <id> --enable`) and tell the human in one line what it does: "I will write this entity and board calendar on the 3rd of every month and build each board pack two weeks before the meeting." They
can change it or turn it off any time; there is nothing to approve.

Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`. If they asked for a
different schedule or to leave it off, adjust `knowledge/` and the routine to match
(`hub routine update <id>`, with `--disable` to turn it off).

Last, run:

    hub bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the
routine run; until then nothing you have runs on its own. Run it once the answers and the first
result are recorded, not before.
