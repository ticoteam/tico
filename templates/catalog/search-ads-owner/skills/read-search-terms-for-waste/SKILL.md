---
name: read-search-terms-for-waste
description: Read a search terms report for the last seven days, find the terms that spent without converting, class each one by what the searcher wanted, and propose the narrowest negative that blocks it, checked against the converting terms first.
---
# Read search terms for waste

When to use: each Monday's Watch, before any negative keyword is proposed, and when a campaign spends over its target with no conversions to explain it.

Steps:
1. Pull the search terms report for the last 7 days and the 7 before, at campaign and ad group level: spend, clicks and conversions for each term, with the conversion action named. Use the primary conversion only.
2. Check the export's total against its rows. A gap is named, not filled.
3. A term is waste when it has spent more than one target cost per result with no conversion in the window, and it has run for at least seven days.
4. Class each waste term by what the searcher wanted: learner (course, training, how to, tutorial); job seeker (jobs, salary, careers, hiring); free seeker (free, cheap, used parts); wrong product (a service the company does not sell); wrong place (a town or region it does not serve); brand confusion (another company with a similar name); unclear. An unclear term is a question for the owner, not a negative.
5. Set the match type to the class: phrase match for a word that marks the class ("course", "jobs"); exact match for a single term that does not repeat a pattern.
6. Check each proposed negative against every converting term in the same export, and against the brand and product list in `knowledge/targets.md`. If it blocks a converting or protected term, narrow it to the exact term or drop it.
7. Write the list in `knowledge/negatives.md`: term, match type, class, spend in the window, the campaign or shared list it goes to, the check result, and the date.
8. Prepare the change with `playbooks/prepare-a-search-ads-change.md`. Adding wasted terms as negatives is upkeep in `knowledge/channel-playbook.md`: add them within a granted Tool, or file the exact list for the person who puts changes live.

Checks before you finish:
- Each negative names its class and the spend it would have blocked in the window.
- No converting term and no brand or product term is blocked. The check ran against the converting terms, not only the waste list.
- Spend is the measure. A term is not waste because it has clicks alone.
- The list records its date, so a later run can tell old negatives from new.

Example (invented, for illustration): "Commuter repairs, last 7 days: 9 terms spent $391 with no conversion. 'bike repair course near me' ($112, learner) and 'bike repair jobs' ($71, job seeker) become the phrase negatives 'course' and 'jobs'. 'free bike repair near me' ($94, free seeker) first became the phrase negative 'free', but the check against the converting terms showed 'free quote bike repair' converted four times. 'free' was dropped, and 'free bike repair near me' is an exact negative instead. The check passed: none of the 14 negatives blocks a converting term."
