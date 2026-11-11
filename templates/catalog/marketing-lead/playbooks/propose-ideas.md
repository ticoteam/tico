# Propose marketing ideas

When: the setup task "Propose the first growth ideas", the weekly meeting when fewer than three ideas are on deck, or the
owner asks. Budget 45 minutes. The outcome is three to eight tasks labelled `idea:on-deck`, each with a channel label and a
source. Nothing is started here; the weekly meeting moves an idea into development.

---

## 1. Read the position

    tico strategy show              bets, Where we play, How we sell, what we will not do
    tico rules list --mine          the rules that apply to you: spend limits, standing instructions
    tico brand summary              positioning and voice, for the wording
    tico growth numbers             the growth numbers, and which channels are behind them
    tico growth channel list        the channels, their stage and owner

Then the market: `tico market show <competitor>` for each competitor, and `tico listening runs --since <30 days ago>` for the
recent matches. Then look at what failed or was inconclusive: the dated learnings in `memory/learnings.md`.

## 2. Look at what did not work first

A failed or inconclusive idea is the best source of a follow-up. A follow-up tests the same customer belief another way.
It is not a retry of the same test.

## 3. Write each idea

Every idea has all of these, or you do not file it:

- **A title** a person can say in one line.
- **A channel**: one or more `channel:<slug>` labels from step 1.
- **A source**: where the signal came from, as a link or an id in the body. No source, no idea.
- **A body** that says what changes, in one or two lines.

No score, prediction or bet is needed, and an idea does not link to a bet.

    tico task create --owner marketing-lead --label idea:on-deck --label channel:<slug> \
      --title "Price calculator on the pricing page" \
      --body "Changes: show the saving against a typical cleaner on the pricing page. Signal: <match id, link or task id>."

A follow-up is filed the same way, then linked to the closed task: `tico task relate <new id> <closed id> --kind follow_up`.

Before you file, scan for a duplicate: `tico task list --label idea:on-deck`. Add to an existing idea with
`tico task comment <id> "..."` instead of filing a copy.

File three to eight. More than eight is a list, not a proposal.

## 4. Finish

    tico task done <id> -m "<count> ideas filed; top three: <titles>; refused: <what and why>" --version N

Ideas stay On deck until the weekly meeting moves one to In development.
