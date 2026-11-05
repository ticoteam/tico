---
name: write-a-changelog-line
description: Write changelog entries for a release, one line per user-visible change, under the six Keep a Changelog types, newest version first, with the pull request reference and an ISO 8601 date.
---
# Write a changelog line

When to use: when a release or a merged set of changes needs a "what changed" entry for users or developers.

Steps:
1. Decide whether a user sees the change. A refactor, a test or an internal rename gets no line. A change to a name, a
   default, a field or a page gets one.
2. Pick one type: **Added** (a new feature), **Changed** (existing behaviour now works differently), **Deprecated** (will be
   removed later), **Removed** (no longer available), **Fixed** (a bug is fixed), **Security** (a vulnerability is fixed).
   If a change could be two types, choose the one a reader would look for first.
3. Write one line: the user-visible effect in one sentence, under about 20 words. Say what the user now gets or sees, not
   how the code changed.
4. Use the name the product shows, from `knowledge/glossary.md`.
5. End the line with the pull request number or link.
6. If the change needs a step from the reader (a migration, a setting to change), link to the how-to. Do not put steps in
   the changelog line.
7. Place the line under its version heading and its type. Newest version first. Changes not yet released go under
   "Unreleased" at the top.
8. Date each version with its release date, in ISO 8601 (`YYYY-MM-DD`). Mark a withdrawn release "[YANKED]".

Checks before you finish:
- Every line has one of the six types and one user-visible effect.
- No internal ticket names, raw IDs or code names appear that a reader cannot use.
- The newest version comes first, and "Unreleased" holds only changes not yet released.
- Dates are ISO 8601.
- Each line carries its pull request reference.

Example (invented, for illustration):

```
## [2.4.0] - 2026-10-14
### Added
- Reminder emails can now go by text as well as email (#398).
### Changed
- Reminders show the class time in the reader's time zone (#402).
### Fixed
- Invoice totals round per line item, not per invoice (#412).
```
