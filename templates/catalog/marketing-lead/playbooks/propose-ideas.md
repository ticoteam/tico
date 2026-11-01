# Propose growth ideas

When: the setup task "Propose the first growth ideas", the weekly meeting when fewer than three ideas are ready to pick, or
the owner asks. Budget 45 minutes. The outcome is three to eight Idea tasks, each with a source and an ICE score, and a
bet whenever the strategy has one.
Nothing is started here; the weekly meeting starts tests.

---

## 1. Read the position

    hub strategy show              bets, what we will not do, goals_by_bet
    hub gtm show                   motion, buyer, channels working now
    hub rules list --mine          the rules that apply to you: spend limits, standing instructions
    hub brand summary              positioning and voice, for the wording
    hub growth economics           CAC, LTV and payback by channel, and the Unknown row
    hub growth evidence            each bet: supports, weakens, none
    hub growth channel list        the channels, their stage and owner

Then the market: `hub market show <competitor>` for each competitor, `hub listening runs --since <30 days ago>` for the
recent matches, then `hub growth idea list --status lost` and `hub growth idea list --status inconclusive`, one list each.

## 2. Look at what did not work first

A lost or inconclusive idea is the best source of a follow-up. A follow-up tests the same customer belief another way.
It is not a retry of the same test.

## 3. Write each idea

Every idea has all of these, or you do not file it:

- **A title** a person can say in one line.
- **A hypothesis** in the form "If we X for Y, Z moves because W".
- **A prediction** with a number and an end date, on the KPI it will be judged on. Write it as "<KPI> from <now> to <number> by <date>".
- **A bet** (`--bet`) when the strategy has bets: then every idea names the one it serves. When it has none yet, file
  without `--bet` and say in the closing note that the bets are missing.
- **A KPI** (`--kpi`), the North Star or the input it moves.
- **A source**: where the signal came from. `--source-kind` is `strategy`, `competitor`, `listening`, `peec`, `lost_test`,
  `person` or `other`; `--source-ref` names the item (a bet id, a market page, a match, an action). No source, no idea.
- **Channels**: one or more `--channel <slug>` from step 1.
- **Segments**: one or more `--segment <id>` from `hub growth segment list`, the customers it targets. Each testing
  segment gets at least one idea that measures its activation or retention.
- **ICE**: `--i` (impact), `--c` (confidence), `--e` (ease), each a whole number from 1 to 10.

    hub growth idea create --title "Price calculator on the pricing page" \
      --hypothesis "If we show the saving against a typical cleaner, more homeowners book, because price is their first question" \
      --prediction "First-clean bookings from price requests 22% to 30% by 2026-12-15" \
      --bet <bet id, when there are bets> --kpi <kpi id> --channel <channel slug> --i 7 --c 6 --e 6 \
      --source-kind listening --source-ref "<match id>"

A follow-up adds `--follow-up-of <lost or inconclusive idea task id>`.

Before you file, check for a duplicate: `hub growth idea list --q "<two words>"`. Update an existing idea instead of
filing a copy: `hub growth idea update <id> ...`.

File three to eight. More than eight is a list, not a proposal.

## 4. Finish

    hub task update <id> --status done --note "<count> ideas filed; top three by ICE: <titles>; refused: <what and why>"

With no bets in the strategy, the note also says so: "No strategy bets yet; ideas filed without one".

Ideas stay at status `idea` until the weekly meeting picks them.
