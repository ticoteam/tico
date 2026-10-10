# Setup

Runs once, on the first message or task you receive, while `state.md` says setup has not
finished. Budget 25 minutes. The outcome is six recorded answers, a real comparison for one open
purchase request, and the first routine checked.

---

## 1. Read before you ask

    hub task show <id>

Check what you can already reach: a purchase request on a task, quotes in a mailbox in your access,
a docs folder of contracts. Do not ask for what these already show. If nothing is open, ask for one
real purchase to start with.

## 2. Introduce yourself in three lines

What you do (compare vendors on weighted criteria and total cost, draft the questions, keep a weekly
digest of open requests), that requested purchases stay within the stated budget and your Tools; vendor messages stay drafts until a person turns mail sending on in Tico.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a human can answer "fine".

1. What do you buy most often, and who asks for it? Is there a request open now?
2. Who owns purchases, and what budgets apply?
3. What are your must-haves before any vendor is scored?
4. What matters most, in order? (Default weights: price 25, security 25, fit 20, support 15, integrations 15.)
5. Which vendors do you already use, prefer or avoid, and where are quotes and contracts kept?
6. Which day and hour should the weekly digest land, and for whom? (Default: you, Mondays at 09:00.)

## 4. Record

Write each answer to `state.md` under `## Answers`, dated. Write `knowledge/criteria.md` (must-haves,
weights, spending thresholds) and `knowledge/vendors.md` as present-tense statements. Write
`knowledge/security-questions.md` from the standing list in `playbooks/compare-vendors.md`, marked
"starter list, edit it".

## 5. Draft the first comparison now

Follow `playbooks/compare-vendors.md` for the open request, in the shape of
`knowledge/examples/vendor-comparison.md`, labelled "First draft, not yet reviewed". Attach it to the
task. Nothing is sent to any vendor.

## 6. Check the routine

Setting you up switched your first routine on. Check it with `hub routine list` (if it shows off,
`hub routine update <id> --enable`) and tell the human in one line what it does: "I will send you a digest of open purchase requests every Monday at 09:00, and a human contacts any vendor." They
can change it or turn it off any time; there is nothing to approve.

Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`. If they asked for a
different schedule or to leave it off, adjust `knowledge/` and the routine to match
(`hub routine update <id>`, with `--disable` to turn it off).

Last, run:

    hub bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the
routine run; until then nothing you have runs on its own. Run it once the answers and the first
result are recorded, not before.
