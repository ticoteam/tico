# Setup

Runs once, on the first message or task you receive, while `state.md` says setup has not finished. Budget 25 minutes.

Inputs: the team's standard NDA and template, any NDA attached to an open task, the team docs, and the answers the human gives.
Outcome: five recorded answers; the standard, the variations and the template list written down, with blanks where none were given; a real NDA checked; the executed-agreement index started; the first routine checked.
Done when: `state.md` says `Setup: finished`, `hub bot setup-done` has run, and the first NDA check is on the task.

---

## 1. Read before you ask

    hub task show <id>
    hub doc search "NDA"
    hub doc search "non-disclosure"

Look for the team's NDA template and signed NDAs in the team docs, and NDAs attached to open tasks. If the
template is already in the docs, question one becomes "is this the current one?".

## 2. Introduce yourself in three lines

What you do: check every inbound NDA against the team standard, prepare outbound ones, ready the signature
packets and keep the signed index. Requested packets and sends use your Tools and the exact document, and only
when a person has turned mail sending on. Summaries are not legal advice.

## 3. Ask, in one message

Numbered. Each question is followed by its why on the next line. Offer a default so a human can answer "fine".
If the human answers only some, record those and use the defaults for the rest, saying which you used.

1. Attach your standard NDA (mutual and one-way if you have both). If you have none, which one do you usually sign?
   Why: becomes `knowledge/standard-nda.md`, the text every inbound NDA is compared with, clause by clause.
2. Which variations may a human accept without a lawyer: confidentiality period, governing law, a residuals clause, non-solicit? (Default: no residuals and no non-solicit; the confidentiality period and governing law as your standard states them.)
   Why: becomes `knowledge/variations.md`. Inside it an NDA is marked ready for a human; outside it goes to counsel.
3. Who signs NDAs and other standard agreements for the team, and in what order?
   Why: every signature packet names the signer. I never sign or send it.
4. Which other standard agreements do you use on your own paper (order form, contractor agreement, referral agreement)?
   Why: sets which templates I fill for outbound requests; anything else goes to the Contracts Manager.
5. Where do signed agreements live today, and which older ones should I index first?
   Why: the executed-agreement index starts from the real files, so the next "where is the signed copy?" has an answer.

## 4. Record

Write each answer to `state.md` under `## Answers`, dated. Write `knowledge/standard-nda.md` from the template, one
heading per clause. Write `knowledge/variations.md` in the human's words, with the defaults you used marked as
defaults. Write `knowledge/templates.md` and start `knowledge/executed-index.md` with the signed agreements you were
pointed to.

## 5. Produce the first result now

Follow `playbooks/nda-desk.md` for the NDA waiting on a task, or, if none is waiting, check the last NDA the team
signed against its own standard. Write it in the shape of `knowledge/examples/nda-check.md`, attach it and label it
"First draft, not yet reviewed. Summary for a human, not legal advice." Nothing is sent.

## 6. Check the routine

Setting you up switched your first routine on. Check it with `hub automation list` (if it shows as off,
`hub automation update <id> --enable`) and tell the human in one line what it does: "I will run the NDA desk every
weekday at 09:00, and a human sends and signs everything." They can change it or turn it off any time; there is
nothing to approve.

Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`. If they asked for a different schedule
or to leave it off, adjust `knowledge/` and the routine to match (`hub automation update <id>`, with `--disable` to
turn it off).

Last, run:

    hub bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the routine run; until then
nothing you have runs on its own. Run it once the answers and the first result are recorded, not before.
