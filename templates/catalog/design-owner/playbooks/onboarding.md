# Setup

Runs once, on the first message or task you receive, while setup has not finished (Tico marks you Needs setup). The outcome is
the brand book, a Brand card proposal built from it, and the first routine checked. It takes the owner about an hour
of answering plus 30 minutes for the look and real pieces; they can stop at any point and you resume.

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
brand changes are proposals the owner publishes, and that the brand book takes about an hour plus 30 minutes, in
as many sittings as they like. Then ask if now is a good time to start; if not, say they can reply "start" whenever.

## 3. Build the brand book

Follow `skills/build-the-brand-book/SKILL.md` from Stage 0. Note the brand book run on the setup task after each stage (stage, next question, open threads) and commit, so a pause loses nothing. When the owner already has a
finished guide, the skill still runs, but Stage 0 pre-fills from it and the questions only cover what it lacks.

## 4. Hand it over

As in the skill's **In Tico** section: `reports/brand-book.md` published with `tico file publish`, the Brand card
proposed with `tico proposal create --kind brand_draft`, a strategy change proposed if the book changed the
positioning, and one `tico task ask` per needs-teammate item. Tell the owner in three lines.

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
