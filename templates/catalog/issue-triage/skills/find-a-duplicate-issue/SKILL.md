---
name: find-a-duplicate-issue
description: Find whether a new issue duplicates an existing one or is only related, using the words of the error, the steps and the area, and decide with a stated match and its evidence sentence rather than by title alone.
---
# Find a duplicate issue

When to use: for every new bug or feature request, before you propose a label or a link. Also when two reporters describe
the same thing in different words.

Steps:
1. Pull the distinctive terms from the new issue: the exact error text, the function or screen named, the feature name,
   and any version string. Leave out generic words ("crash", "broken", "bug", "not working").
2. Search the repository for each term, open and closed, over the last twelve months:
   `gh search issues "<term>" -R <repo>`. Leave out `--state`: without it the search returns open and closed issues. Search for
   the error text in quotes first.
3. For each candidate, read its steps and its last comments, not only its title. A title match with different steps is
   related, not a duplicate.
4. Compare on three things: the same trigger (the same action or input), the same visible result, and the same area. All
   three match: a duplicate candidate. Two of three: related.
5. Score the match with the decision model the playbook names (`hub decision ask`). At 0.7 or above, propose "duplicate of
   #n" and quote the sentence from each issue that shows the match. Between 0.5 and 0.7, propose "possibly related" and
   ask the requester. Below 0.5, propose nothing.
6. Choose the canonical issue: the older one that already has the steps, the logs or the most discussion. Never propose
   closing either issue; a maintainer decides.
7. If the new report has information the canonical one lacks (a version, a log, a new environment), say which to copy
   across.

Checks before you finish:
- Each duplicate proposal names both numbers and the matching sentence from each.
- A title match with different steps is marked related, not duplicate.
- The search terms are listed in the note, so a person can repeat the search.
- Security reports are never compared in public. Matching is done on the internal note only.

Example (invented, for illustration): "#1424 duplicates #1421 (score 0.82). Both say bookings vanish after 'Sync now' on
the calendar screen, on version 2.14. #1421 has the logs; copy the device model from #1424. Proposed: duplicate of #1421."
