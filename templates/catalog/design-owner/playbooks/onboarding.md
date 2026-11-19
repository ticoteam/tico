# Setup

Runs once, on the first message or task you receive, while setup has not finished (Tico marks you Needs setup). The outcome is
the brand book, a Brand card proposal built from it, and the first routine checked. You draft from what the team
already wrote; the owner answers one message of taste calls and marks the real pieces, whenever suits them.

---

## 1. Read before you ask

    tico task show <id>
    tico brand show
    tico strategy show
    tico doc search "brand"
    tico doc search "style guide"

Read any guide you find, and the team's home page. Do not ask what these already say.

## 2. Introduce yourself in three lines

What you do (the brand book, reviews of copy and assets against it, a monthly audit of what went public), that
brand changes are proposals the owner publishes, and that you draft the brand book from what exists and ask only
the taste calls. Start at once; do not wait for a reply.

## 3. Build the brand book

Follow `.claude/skills/build-the-brand-guide/SKILL.md`, Build mode. Note progress on the setup task after each step (step, open taste calls, open threads) and commit, so a pause loses nothing. When the owner already has a
finished guide, the skill still runs, but step 1 starts from it and the questions only cover what it lacks.

## 4. Hand it over

As in the skill's step 11: `knowledge/brand.md` written, `reports/brand-guide.html` published with `tico page publish`, the Brand card
proposed with `tico proposal create --kind brand_draft`, a strategy change proposed if the book changed the
positioning, and one sub-task per missing fact to the teammate who holds it. Tell the owner in three lines.

## 5. Check the routine

Setting you up switched your monthly audit on. Check it with `tico automation list` (if it shows off,
`tico automation update <id> --enable`) and tell the owner in one line: "I will audit what went public on the 1st of
each month, against the brand card." They can change it or turn it off any time; there is nothing to approve.

Record it in `memory/decisions.md`. If they asked for a different schedule
or to leave it off, adjust the routine to match (`tico automation update <id>`, with `--disable` to turn it off).

Last, run:

    tico bot setup-done

It tells {{app_name}} your setup is done and clears your "Needs setup" mark. Run it once the book is handed over,
not before. If the owner stops partway and says to finish later, leave setup open: the mark reminds them.
