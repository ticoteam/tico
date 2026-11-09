# Prepare a search ads change

Triggered by a task asking for a new campaign or ad group, a keyword, a negative, a bid or budget move, a bidding strategy or target, a copy or landing page test, or a diagnosis ("why did cost per result jump?"), and by a change the weekly review proposes. Budget 30 minutes. The outcome is one change, prepared completely and waiting for review: no more than 150 words on the task. A change is applied only when the task asks for it and a granted Tool allows it.

---

## 1. Read the request

    hub task show <id>

Find the segment, the goal, the platform, the campaign or ad group, the budget and the date. If one of them is missing, ask once with `hub task question "<question>" --on <person> --parent <id>` and stop.

## 2. Check it against the targets and the structure

Read `knowledge/targets.md`, `knowledge/structure.md`, `knowledge/negatives.md` and `knowledge/changes.md`. If a similar change was tried, say what happened. If the request breaks a ceiling, or would block a brand or product term, say so on the task and stop.

## 3. Build it

- **New campaign or ad group:** the segment, the platform, the theme and its match types, the negatives it starts with, the bidding strategy and why its volume supports it (`skills/choose-a-bidding-strategy-from-volume/SKILL.md`), the daily budget, the primary conversion and its target, the review date, and the landing page. Read the landing page. If it does not match the search, route it to `cro-owner`.
- **Keyword:** the term, its match type, the ad group it joins, its estimated volume from the platform, and the expected cost per result on the same basis as the target.
- **Negatives:** each term, its match type, its class, the spend it blocks in the window, the list it goes to, and its check against the converting terms (`skills/read-search-terms-for-waste/SKILL.md`).
- **Bid or budget move:** from which campaign to which, how much, within the step default in `knowledge/targets.md`, and the evidence from the last 28 days. A move between segments, or between search and social, is the marketing lead's decision (`growth-team-manager`): ask it on the task.
- **Copy or landing page test:** the question, the variants, the primary conversion, the count and the earliest read date (`skills/test-ad-copy-and-landing-page-fit/SKILL.md`).
- **Diagnosis:** the numbers before and after, the factor that moved (price of a click, or conversion rate), the most likely cause, and the check that would confirm it.

## 4. Check the claims

Before any new copy goes in, list each claim with its dated basis and its category, as the Claims section of `knowledge/paid-search-method.md` sets out. A claim with no basis is removed or changed to what can be shown. Record the approved copy and its sources in `knowledge/copy.md` before it is published.

## 5. Put it up for review

On the task, in under 150 words: the change, the money, the evidence, how it will be judged and when. Name the person who reviews it: the requester for a campaign, or the owner for a routine change. Record it in `knowledge/changes.md` as "proposed".

## 6. Apply it when it is asked for

Only when the task asks for it and a granted Tool allows it: apply the change in the account, then record who applied it and when in `knowledge/changes.md`, and add the two-week check to the next review. Ad copy with a claim in it stays a draft on the task until it is applied under the same gate. Finish with `hub task update <id> --status done --note "..."`.
