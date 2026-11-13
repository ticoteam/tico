# Setup

Runs once, on the first message or task you receive, while setup has not finished (Tico marks you Needs setup). Budget 25 minutes. The outcome is five recorded answers, the policy turned into checks, the
open requests worked, a first returns report, and the first routine checked.

Inputs: the task list, `tico team show`, the open return and refund requests, and the policy the Librarian cites.

Done when: the five answers are in `knowledge/`, the policy checks and the ledger are written, the open requests have a decision each, the first report is on the task, the routine is checked, and `tico bot setup-done` has run.

---

## 1. Read before you ask

    tico team show
    tico task list
    tico doc ask "What is our return and refund policy?"

Collect the open return and refund requests. The policy the Librarian cites, with its date, is the
starting point for question one. Do not ask what these already show.

## 2. Introduce yourself in three lines

What you do (check each return against the policy and the order, prepare the reply and the refund, a
weekly report on why things come back), that requested refunds and labels follow the policy and your Tools; replies stay drafts until a person turns mail sending on in Tico, and you never refuse what the policy allows.

## 3. Ask, in one message

Numbered, each with its one-line why, offering the defaults.

1. What is the return and refund policy, and where is it written?
2. Where can I read orders?
3. Which return cases does the written policy cover, and what information is needed for exceptions?
4. Who owns refunds and replies, and who issues them in the shop or payment system?
5. When should the weekly report land, and who reads it? (Default: Mondays 09:00, you.)

## 4. Record

Record each answer, dated, in the `knowledge/` file it belongs to. Write `knowledge/policy-checks.md`: each
rule as a check, with its source. Start `knowledge/ledger.md` from the open requests.

## 5. Produce the first result now

Run `playbooks/decide-a-return.md` on each open request, then `playbooks/weekly-returns-report.md`.
Label the report "First draft, not yet reviewed". Issue nothing.

## 6. Check the routine

Setting you up switched your first routine on. Check it with `tico automation list` (if it shows off,
`tico automation update <id> --enable`) and tell the human in one line what it does: "I will write this report every Monday at 09:00." They
can change it or turn it off any time; there is nothing to approve.

Record it in `memory/decisions.md`. If they asked for a
different schedule or to leave it off, adjust `knowledge/` and the routine to match
(`tico automation update <id>`, with `--disable` to turn it off).

Last, run:

    tico bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the
routine run; until then nothing you have runs on its own. Run it once the answers and the first
result are recorded, not before.
