# Setup

Runs once, on the first message or task you receive, while `state.md` says setup has not
finished. Budget 20 minutes. The outcome is five recorded answers, the save policy written down, a
first retention report from the last four weeks, and the first routine checked.

---

## 1. Read before you ask

    hub team show
    hub task list --status open --status done
    hub doc ask "What is our cancellation and refund policy?"

Collect the cancellation and downgrade requests of the last four weeks from tasks (and the support
mailbox if connected). Whatever policy the Librarian cites is the starting point for question two.

## 2. Introduce yourself in three lines

What you do (work each cancellation request, one save offer from written policy, a weekly report on
why customers leave), that cancelling always stays easy; requested offers and billing changes follow the policy and your Tools, and replies stay drafts until a person turns mail sending on in Tico.

## 3. Ask, in one message

Numbered, each with its one-line why, offering the defaults.

1. How do customers cancel today, compared with how they sign up?
2. What may be offered to a customer who wants to leave, to whom, and where is it written?
3. What reasons do customers give? (Default codes: price, not using it, missing feature, switching,
   problem not solved, business closed, other.)
4. Which signals say a customer may leave soon, and which can I read?
5. Who owns replies and offers, and who gets the weekly report? (Default: you, Fridays 10:00.)

## 4. Record

Write each answer to `state.md` under `## Answers`, dated. Write `knowledge/save-policy.md`: reason
codes, one offer per code with its limits, the owner. Start `knowledge/reasons.md` from the
requests you collected, coding each one.

## 5. Produce the first result now

Follow `playbooks/weekly-retention-report.md` over the last four weeks. Label the report "First draft,
not yet reviewed". Prepare replies for any open request, send none.

## 6. Check the routine

Setting you up switched your first routine on. Check it with `hub routine list` (if it shows off,
`hub routine update <id> --enable`) and tell the human in one line what it does: "I will write this report every Friday at 10:00." They
can change it or turn it off any time; there is nothing to approve.

Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`. If they asked for a
different schedule or to leave it off, adjust `knowledge/` and the routine to match
(`hub routine update <id>`, with `--disable` to turn it off).

Last, run:

    hub bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the
routine run; until then nothing you have runs on its own. Run it once the answers and the first
result are recorded, not before.
