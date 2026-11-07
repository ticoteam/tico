# Paid search method

The standing rules behind the weekly review, the structure and negatives, and every change proposal. Thresholds are platform guidance or dated third-party figures, and each line says which. Figures carried from the paid-media template's method were read there on 2026-11-01; this file did not re-read them. Team rules (the step size for budget moves, the test count, the review thresholds) are this company's choices, set in `knowledge/targets.md`, and are not platform rules.

## Measure the result, not the click

- One primary conversion per campaign, named in `knowledge/targets.md`. Clicks, impressions and impression share explain a result; they are not one.
- Mark each conversion action primary or secondary in each platform. A secondary action set as primary (a page view, a call that is not a booking) inflates the result.
- Google and Microsoft count overlapping searches. Report each platform's count under its name, never summed. Where the CRM holds a de-duplicated count, show it beside them with its source.
- Calls, form fills, bookings and store installs are different results. If a platform counts calls as conversions, show them on their own line.
- App store search ads count installs and in-app events. A cost per install is not a cost per paying customer. Report the event the goal names, and say which one it is.
- Impression share below 10 per cent or above 90 per cent is reported as a bound ("under 10 per cent"), never as an exact figure (carried from the paid-media method, read 2026-11-01).
- Search term reports lag, and platforms restate recent days. Record the time of each pull with the reading.
- Compare like with like. A cost per result is spend over conversions for the same conversion action and the same days. Changing the conversion definition starts a new series.

## Structure and match types

- One theme per ad group: the searches that one ad answers. A repair search and a job search in one ad group make the ad and its negatives impossible to read.
- Match types: exact match for a proven term worth protecting; phrase match for a theme; broad match only under a conversion-based bidding strategy with a maintained negative list. A broad keyword with no negatives buys searches the business never meant. This is general practice; check each platform's current match-type help at setup.
- Separate campaigns where targets differ. A consumer repair and a fleet quote have different targets, so they never share a budget.
- Brand terms sit in their own campaign and are reported apart. Brand cost per result is not compared with a non-brand target.

## Diagnosing a cost per result

Cost per result = average cost per click ÷ conversion rate, where conversion rate = conversions ÷ clicks (computed here, not sourced). A rise therefore has three possible sources: the price of a click (auction and bid), the relevance of the clicks (the query mix and the ad), or what happens after the click (the page, the offer and tracking). The search terms report shows which queries changed. If the cost per click is steady and the conversion rate falls, check the terms first, then the page (`cro-owner`), then the conversion setup (`marketing-ops`).

## Negatives

- Read the search terms report every week (`skills/read-search-terms-for-waste/SKILL.md`).
- Add a negative at the narrowest level that works: one campaign, or a shared list by class (learner, job seeker, free seeker, wrong product, wrong place). A list for the whole account only when the class fails in every campaign.
- Check every negative against the converting terms in the same export before it is proposed. A word that blocks a converting term is replaced by the exact term.
- Never block a brand or product term, or a term the price list uses. Team rule.
- A competitor's name is bid on only where the target allows it and the platform's trademark rules are checked. Each such keyword is a change, prepared for review.

## Bidding for the volume

- Tracking comes first. A strategy that optimises for a conversion needs that conversion recorded and counted once.
- Google's Smart Bidding best-practice guidance sets a minimum of 15 conversions with values in the last 30 days for Target ROAS. Third-party guides put a workable Target CPA at about 30 or more conversions a month. Use the platform's own figure when the account shows one (carried from the paid-media method, read 2026-11-01).
- Under the minimum, keep a manual or enhanced bid with a cap on the cost per click, and read the result over 30 days, not one week. A target the volume cannot train starves delivery.
- Set a target at the 90-day average cost per result, not below it. A target far below history starves delivery (third-party guidance, 2025-2026).
- A bidding strategy change restarts learning. Do not change it inside a learning period unless something unusual shows.
- A campaign that loses impression share to budget while under its target is a candidate for a budget move within the channel. A move between segments, or between search and social, is the marketing lead's decision (`marketing-lead`).

## Ad copy and landing pages

- Each copy test changes one idea: the offer or the price, the proof, the pain or the call to action. Two changes in one variant cannot be read.
- Responsive ads combine headlines and descriptions, so a test of one headline is not readable. Test whole ads, or pin the one headline under test and say that it is pinned.
- Do not call a winner from under seven days or under 30 primary conversions per variant (team rule in `knowledge/targets.md`).
- Read a gap against its spread. With n conversions in one variant and m in the other, one standard error of the gap is about the square root of (1/n + 1/m), in relative terms: about 26 per cent at 30 each. Treat a gap under two standard errors (about 52 per cent at 30 each) as no winner yet. This is arithmetic, not a platform rule.
- The landing page's first screen must show the same service, the same price or offer and the same action as the ad. A page that does not match goes to `cro-owner` with the search terms and the ad text as evidence.
- Quality score and ad strength are platform labels, not results. Read a quality score for its parts (expected click-through, ad relevance and landing page experience) as a diagnostic; judge copy on cost per result.

## Claims and policies

- Every claim is one a dated source supports: a price with its period, a saving with its baseline, a rating with its source and date. A claim with no basis is removed, not softened.
- Brand and competitor names in ad text need the platform's trademark rules checked before launch.
- Restricted categories (for example healthcare, financial services, gambling and alcohol) have their own limits and checks in each platform's ad policy. Check the policy before launch; a misclassified ad can be rejected or restricted after it runs.

## Sources

- Google Ads Smart Bidding best practices (current PDF, read 2026-11-01, carried from paid-media): the 15-conversion Target ROAS minimum.
- Third-party bidding guides, 2026 (read 2026-11-01, carried from paid-media): ranges only, not platform rules.
- Account reporting notes from a live paid bot (censored impression share, reporting lag, overlapping platform counts), 2026, carried from paid-media. The rules are general; the figures are not.
- Google and Microsoft ad policy and help pages, and Apple's search ads documentation, were not read for this template. The match-type, call and bidding rules above are general practice. Check each platform's current help before a change depends on one.
- The cost-per-result identity and the standard-error spread are arithmetic computed here, not sourced.
