# Prepare a search ads change

How to make the real thing for a search ads bet, and how to write the exact change for upkeep. Used inside a Building
task (`playbooks/build-a-bet.md`, "Make the real thing"), by Watch and Fix a slip for upkeep, and when a task asks for
a campaign plan or a diagnosis ("why did cost per result jump?"). Budget 30 minutes.

The outcome is one change, complete and exact: what goes into the account, where, from what to what, and its
evidence. Nothing goes into the account here. A bet goes live only after a person OKs its Go-live page
(`playbooks/run-and-measure.md`); upkeep follows the list in `knowledge/channel-playbook.md`.

---

## 1. Read the request

    tico task show <id>

Find the segment, the goal, the platform, the campaign or ad group, the budget and the date. If one of them is
missing, ask once with `tico task ask <id> --to <person> "<question>" --version N` and stop.

## 2. Check it against the targets and the structure

Read `knowledge/targets.md`, `knowledge/structure.md`, `knowledge/negatives.md` and `knowledge/changes.md`. If a
similar change was tried, say what happened. If the request breaks a ceiling, would block a brand or product term, or
would raise a budget outside an approved experiment, say so on the task and stop.

## 3. Build it

- **New campaign or ad group:** the segment, the platform, the theme and its match types, the negatives it starts with,
  the bidding strategy and why its volume supports it (`skills/choose-a-bidding-strategy-from-volume/SKILL.md`), the
  daily budget, the primary conversion and its target, and the landing page. Read the landing page. If it does not
  match the search, file a sub-task to `cro-owner`.
- **Keyword:** the term, its match type, the ad group it joins, its estimated volume from the platform, and the
  expected cost per result on the same basis as the target.
- **Negatives:** each term, its match type, its class, the spend it blocks in the window, the list it goes to, and its
  check against the converting terms (`skills/read-search-terms-for-waste/SKILL.md`).
- **Bid or budget move:** from which campaign to which, how much, and the evidence from the last 28 days. A budget
  raise is always a bet. A move between segments, or between search and social, is the Growth Team Manager's decision
  (`growth-team-manager`): ask it on the task.
- **Ad copy or landing page test:** the question, the variants as they will show, the primary conversion, the count and
  the earliest read date (`skills/test-ad-copy-and-landing-page-fit/SKILL.md`).
- **Pause or broken final URL (upkeep):** the ad or campaign, the rule or the broken page it breaks, and what it
  changes back to.
- **Diagnosis:** the numbers before and after, the factor that moved (price of a click, or conversion rate), the most
  likely cause, and the check that would confirm it.

## 4. Check the claims

Before any new copy, list each claim with its dated basis and its category, as the Claims section of
`knowledge/paid-search-method.md` sets out. A claim with no basis is removed or changed to what can be shown. Record the
copy and its sources in `knowledge/copy.md`.

## 5. Hand it on

- **In a Building task:** attach the drafts to the task, and put the change as it will look on the Go-live page
  (`playbooks/build-a-bet.md`, step 4): the keywords, each ad as it will show, the landing page, the budget, the stop
  rule. Record it in `knowledge/changes.md` as "proposed".
- **For upkeep:** make the change within a granted Tool, or file it for the person who puts changes live:

      tico task create --owner <person id> --label channel:search-ads --title "Apply: <the change>" --body "<exactly what to change, where, and why>"

  Record it in `knowledge/changes.md` as "made" or "filed", with the date.
- **For a plan or a diagnosis:** answer on the task in under 150 words. A plan someone wants built becomes an idea in
  Growth Experiments with your channel tag.

## 6. After it is live

Once it goes live (`playbooks/run-and-measure.md`), record who put it in and when in `knowledge/changes.md`. Two weeks
after an upkeep change, write what the numbers did beside it.
