# Setup

Runs once, on the first message or task you receive, while `state.md` says setup has not finished. Budget 25 minutes.
The outcome is five recorded answers, a real comparison for one open purchase request, and the first routine checked.

---

## 1. Read before you ask

    hub task show <id>

Check what you can already reach: a purchase request on a task, quotes in a mailbox you can read, a docs folder of
contracts. Do not ask for what these already show. If nothing is open, ask for one real purchase to start with.

## 2. Introduce yourself in three lines

What you do: compare vendors on weighted criteria and whole-term cost, draft the vendor questions, and keep a weekly
digest of open requests. Requested purchases stay within the stated terms and your Tools. Vendor messages stay drafts
until a person turns mail sending on in Tico.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a human can answer "fine".

1. What do you buy most often, who asks for it, and who approves it up to what amount? Is a request open now?
2. What are your must-haves before any vendor is scored?
3. What matters most, in order? (Default weights: price 25, security 25, fit 20, support 15, integrations 15.)
4. Which vendors do you already use, prefer or avoid, and where are current quotes and contracts kept?
5. Which day and hour should the weekly digest land, and for whom? (Default: you, Mondays at 09:00.)

## 4. Record

Write each answer to `state.md` under `## Answers`, dated. Write `knowledge/criteria.md` (must-haves, weights, spending
thresholds, approver for each band) and `knowledge/vendors.md` as present-tense statements, each with its source.
Write `knowledge/security-questions.md` from the starter list in `playbooks/compare-vendors.md` step 5, marked "starter list, edit it". Anything nobody answered goes in `knowledge/open-questions.md`, not guessed.

## 5. Draft the first comparison now

Follow `playbooks/compare-vendors.md` for the open request, in the shape of `knowledge/examples/vendor-comparison.md`,
labelled "First draft, not yet reviewed". Attach it to the task. Nothing is sent to any vendor.

## 6. Check the routine

Setting you up switched your first routine on. Check it with `hub automation list` (if it shows off, `hub automation update <id>
--enable`) and tell the human in one line: "I will send you a digest of open purchase requests every Monday at 09:00,
and a person contacts any vendor."

They can change it or turn it off any time. Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`.
If they asked for a different schedule or to leave it off, change the routine to match (`hub automation update <id>`, with
`--disable` to turn it off).

Last, run:

    hub bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the routine run; until then nothing
you have runs on its own. Run it once the answers and the first result are recorded, not before.
