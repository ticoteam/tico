# Search Ads playbook

How {{company_name}} runs search ads, and what the Search Ads Owner may do by itself. This is the starter: the Search
Ads Owner fills in the blanks at setup and publishes it as the page "Search Ads playbook". To change a line, tell the
Search Ads Owner on a task; it changes this file and publishes the page again.

## The basics

- **Channel tag:** `channel:search-ads`. Every search ads task carries it.
- **KPI:** cost per new customer from search ads. Spend from the ads account, new customers from the CRM or signup
  records. Read weekly. Lower is better. It sits under the North Star.
- **Tools:** the ads account (Google Ads, Microsoft Ads). The CRM or signup records and the public web when connected.
  Read is enough to start.
- **Who puts changes live:** (filled at setup: the person who changes the ads account). Until a Tool lets the Search
  Ads Owner change the account, this person applies each change a person OK'd, and each piece of upkeep below.
- **Anything that must never change:** (filled at setup: campaigns, terms or settings nobody touches).
- **Ceilings and protected terms:** in `knowledge/targets.md`, from setup.

## What Watch checks

Daily at 07:00. Mondays add the weekly checks.

- Every day: spend and cost per result for each kept campaign, the last 7 days against its range. A campaign out of
  range moves to Slipping.
- Mondays: the search terms report for the last 7 days. Terms that spent with no result are read for waste
  (`skills/read-search-terms-for-waste/SKILL.md`), and the wasted ones excluded as upkeep.
- Mondays: the KPI, cost per new customer from search ads, for last week.

## Where ideas come from

- The search terms report: searches that bring customers but have no keyword or ad of their own.
- Campaigns under target that lose impression share to budget: a bet to raise the budget, inside an experiment.
- Bidding strategies the conversion count now supports, or no longer supports
  (`skills/choose-a-bidding-strategy-from-volume/SKILL.md`).
- Ads whose landing page does not match the search (`skills/test-ad-copy-and-landing-page-fit/SKILL.md`).
- What competitors bid on and say in their ads (`tico market delta`).
- Kept campaigns that were retired: what to try instead.

## Upkeep the Search Ads Owner does by itself

No person's OK needed. Done within granted Tools, or filed with the exact change for the person who puts changes live.

- Add wasted search terms as negative keywords, on Mondays, each checked against the converting terms and the protected
  terms first.
- Pause an ad or a campaign that breaks a cutoff set in Rules, or that sends people to a broken page.
- Fix a broken final URL, so the ad points at the page a person OK'd.

## What needs a person

Each of these is a bet. A person OKs its Go-live page first.

- Any budget raise. Budgets go up only inside an approved experiment.
- A new campaign, a new ad group or a new set of keywords.
- New ad copy.
- A change of bidding strategy or target.
- Anything on the "never change" list above.

## Cutoffs: set these in Rules

One cutoff for search ads: "Ads pause when cost per new customer passes N times its 30-day average."

It is off until a person sets N in Rules. {{app_name}} does not turn it on for you. The Search Ads Owner can suggest it with the
number a person gives, and a person accepts it in Rules. Once it is on, Watch pauses an ad or campaign that passes it,
as upkeep, and says so on its task.

With or without the rule, the Search Ads Owner may always pause an ad or campaign by itself when its money is going to
waste. It never raises a budget outside an approved experiment.

## Never

- Raise a budget outside an approved experiment, or spend past a ceiling.
- Block a brand or product term, or a term the price list uses.
- Run an ad with a claim that has no dated source.
- Write a number without its dated source.
