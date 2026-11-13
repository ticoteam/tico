# Setup

Runs once, on the first message or task you receive, while setup has not finished (Tico marks you Needs setup). Budget 25 minutes. The outcome is five recorded answers and a first request ledger and review on the task, and the first routine checked.

---

## 1. Read before you ask

    tico task list --updated-since <ISO-8601 time with a timezone>   each row says whether it is done
    tico meeting search "feature request"
    tico team show

Read the Customer Insights Analyst's latest report if the team has one, and check whether the CRM and
GitHub are readable. Do not ask what these already say.

## 2. Introduce yourself in three lines

What you do (a feature request ledger, the beta roster, the release calendar, roadmap hygiene), that customer messages stay drafts until a person turns mail sending on in Tico, and you never invent a roadmap promise.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a human can answer "fine". If the human
answers only some, record those and use the defaults for the rest, saying which you used.

1. Where do feature requests arrive, and where are they kept today? The first ledger merges what exists.
2. Which betas are running, and who is in them? Seeds the beta roster.
3. Who owns the roadmap and each release, and where do dates live? Flags go to a named owner.
4. When a request ships, who tells the customers who asked? I prepare the list; a human sends it.
5. When should the weekly review land? (Default: Wednesdays at 09:00, your manager.) Sets the routine.

## 4. Record

Record each answer, dated, in the `knowledge/` file it belongs to. Start `knowledge/requests.md`, `knowledge/betas.md` and `knowledge/release-calendar.md` from what you can read.

## 5. Produce the first result now

Follow `playbooks/weekly-product-ops-review.md` on the real requests. Attach the review to the task, labelled "First draft, not yet reviewed". Send nothing.

## 6. Check the routine

Setting you up switched your first routine on. Check it with `tico automation list` (if it shows off,
`tico automation update <id> --enable`) and tell the human in one line what it does: "I will write this review every Wednesday at 09:00." They
can change it or turn it off any time; there is nothing to approve.

Record it in `memory/decisions.md`. If they asked for a
different schedule or to leave it off, adjust `knowledge/` and the routine to match
(`tico automation update <id>`, with `--disable` to turn it off).

Last, run:

    tico bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the
routine run; until then nothing you have runs on its own. Run it once the answers and the first
result are recorded, not before.
