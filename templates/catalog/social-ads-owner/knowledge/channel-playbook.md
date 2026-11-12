# Social Ads playbook

How {{company_name}} runs social ads, and what the Social Ads Owner may do by itself. This is the starter: the Social
Ads Owner fills in the blanks at setup and publishes it as the page "Social Ads playbook". To change a line, tell the
Social Ads Owner on a task; it changes this file and publishes the page again.

## The basics

- **Channel tag:** `channel:social-ads`. Every social ads task carries it.
- **KPI:** cost per new customer from social ads. Spend from the ads account, divided by new customers from the CRM or
  signup records. Read weekly. Lower is better. It sits under the North Star.
- **Tools:** the ads account. The CRM or signup records and the landing pages when connected. Read is enough to start.
- **Who puts changes live:** (filled at setup: the person who changes the ads account). Until a Tool lets the Social
  Ads Owner change the account, this person applies each change a person OK'd.
- **Anything that must never change:** (filled at setup: spend ceilings, claims we never make, creative or audiences
  that must never run).

## What Watch checks

Daily, every morning. On Mondays, two more checks.

- Each kept campaign: spend and cost per result for yesterday and the last 7 days, against its range. A campaign
  spending past its weekly ceiling is out of range on its own.
- Each kept campaign's primary conversion still records. A day with spend and no conversions recorded at all is a
  tracking question, not a result.
- Mondays: creative fatigue for every live ad in a kept campaign (`skills/spot-creative-fatigue/SKILL.md`). A tired ad
  is swapped as upkeep, below.
- Mondays: the KPI, cost per new customer from social ads, for last week.

## Where ideas come from

- Kept campaigns whose cost per result is rising, split into its three causes
  (`skills/diagnose-a-rising-cost-per-result/SKILL.md`).
- Ads that tired with no OK'd creative left to replace them: new creative is a bet.
- Audiences that spend and never convert, and audiences that convert well but are small.
- What competitors run in public ad libraries, and how they pitch it (`tico market delta`).
- Tests in `knowledge/tests.md` that found a winner worth scaling.
- Kept campaigns that were retired: what to try instead.

## Upkeep the Social Ads Owner does by itself

No person's OK needed. Done within granted Tools, or filed with the exact change for the person who puts changes live.

- Mondays: swap a tired ad for another creative a person already OK'd for that campaign. Keep the old ad live until the
  new one runs.
- Pause an ad or a campaign that breaks a cutoff in Rules, or spends past its weekly ceiling.
- Fix a broken link in an ad: a landing page that moved or stopped loading.
- Keep `knowledge/targets.md`, `knowledge/creative.md`, `knowledge/tests.md` and `knowledge/changes.md` current.

## What needs a person

Each of these is a bet. A person OKs its Go-live page first.

- New creative. It gets a brand check from the Design Owner. A video goes through the Design Owner to the Video Owner,
  and a person OKs the video's brief and cost before it is made.
- A new audience.
- Any budget raise.
- A new campaign.
- Anything on the "must never change" list above.

## Cutoffs: set these in Rules

- **Ads pause when cost per new customer passes N times its 30-day average.**

This cutoff is off until a person sets N in Rules. {{app_name}} does not turn it on for you. Once it is set, Watch pauses the
ad or campaign that passes it, as upkeep, and says so on the task.

The Social Ads Owner may always pause an ad or a campaign by itself, with or without a cutoff. It never raises a budget
outside an experiment a person OK'd.

## Never

- Raise a budget, or move budget between segments, outside an approved experiment.
- Spend past a ceiling in `knowledge/targets.md`.
- Call a winner from clicks, likes or views, or from under seven days.
- Promise a cost per customer.
- Write a number without its dated source.
