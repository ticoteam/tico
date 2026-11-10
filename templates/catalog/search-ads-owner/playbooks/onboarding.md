# Setup

Runs once, on the first message or task you receive, while `state.md` says setup has not finished. Budget 20 minutes. The outcome is five recorded answers, a first review on the task from a real export, and the first routine checked.

---

## 1. Read before you ask

    tico task show <id>
    tico goal list --all
    tico market segment tree

Check whether an export is attached and which Tools this bot holds (`tico tool list --bot <slug>`). Note each segment, and each goal that names leads, trials, quotes or sales: they tell you which conversions matter. Do not ask what these already say.

## 2. Introduce yourself in three lines

What you do (a weekly review of every paid search campaign by segment and platform, search-term waste, bidding checked against conversion volume, and three changes prepared for review). That every change waits for a task that asks for it and a granted Tool that allows it, and that ad copy is checked for claims before it goes live.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a human can answer "fine".

1. Which search platforms do you buy (Google Ads, Microsoft Ads, app store search ads), roughly what do you spend a month on each, and who can change those accounts today?
2. For each segment, what result do the searches buy (a booked job, a quote, a trial, an install that becomes a paying customer), and what may one cost? (Default: last 90 days' average cost per result.)
3. Which conversion does each campaign count, and which one is primary? Should phone calls and store installs count as results?
4. What are your weekly spend ceilings, which brand, product and competitor terms are protected or off limits, and which claims may we never make?
5. How should the weekly exports reach me? (Default: the campaign, ad group and search terms exports for the last 7 days, attached to the task.)

## 4. Record

Write each answer to `state.md` under `## Answers`, dated. Write `knowledge/targets.md`: one block per segment and campaign (segment, purpose, primary conversion, target cost per result, weekly ceiling, owner, campaign name prefix), then the protected terms, the competitor rules, the never-change rules, the budget step (default 20 per cent) and the test count (default 30 primary conversions per variant). Create `knowledge/structure.md`, `knowledge/negatives.md`, `knowledge/copy.md`, `knowledge/tests.md` and `knowledge/changes.md` with their headings and nothing else.

## 5. Produce the first result now

Follow `playbooks/weekly-search-ads-review.md` on the attached exports. Write `reports/YYYY-MM-DD-search-ads.md`, attach it to the task and label it "First draft, not yet reviewed". Keep this first result a draft and report the checks.

## 6. Check the routine

Setting you up switched your first routine on. Check it with `tico automation list`. If it shows off, run `tico automation update <id> --enable`. Tell the human in one line what it does: "I will review every paid search campaign each Monday at 09:30 from the exports you attach." They can change it or turn it off at any time; there is nothing to approve. Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`.

## 7. Finish setup

Once the answers and the first result are recorded, not before, tell the task that setup is done, with the report path and the routine's next run date. Then run the command below. It clears your "Needs setup" mark, and until it runs nothing you have runs on its own.

    tico bot setup-done
