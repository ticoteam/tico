---
name: build-a-sourcing-search
description: Turn a role's required criteria into a short list of public places where people with that work show up, write the search strings for each, and record what each one yielded, so the search gets better each week.
---
# Build a sourcing search

When to use: when a role is opened for sourcing, when a search has yielded fewer than three profiles in a week, or when the
criteria change.

Steps:
1. Copy the required criteria from `knowledge/roles/<role>.md` (or the hiring manager's brief) into `knowledge/searches/<role>.md`,
   with the date. Use the criteria as written. Do not add a school, a company or a title that the criteria do not name.
2. Turn each required criterion into the work it describes. "Ten years in property management" becomes the tasks a
   property manager does. Name the public places where that work is shown: conference talks, open-source projects, technical
   write-ups, portfolios, community groups, professional associations' directories.
3. Rank the places by how close their members are to the work. Start with the two places most likely to hold the work
   itself, not the place most people use.
4. For each place, write one or two search strings. Keep each one short, and record the exact string you ran. Record the
   date.
5. Stop at 15 profiles per role. Once a source has returned five profiles in a row that fail the criteria, stop that source for
   the week and say so.
6. After the week, record for each source and string: profiles returned, profiles that showed the required criteria, replies
   to outreach. Move the next week's effort to the sources with replies, not volume.
7. If a source needs a login or blocks automated reading, name it as not read and move on. Do not work around a login wall.

Checks before you finish:
- Every search string is in the file with its date.
- Every place named is public. Nothing needed a login.
- The criteria in the file match the role file's date.
- Each source has a yield line (profiles, criteria shown, replies), or "not run this week".

Example (generic, invented): Role: Data Engineer. Required: builds batch pipelines in production. Place one: open-source
scheduler maintainers. String: `"pipeline" "backfill" site:github.com` (run 2026-11-01): 9 returned, 4 with a production
write-up linked. Place two: a regional data meetup's past talk list. Yield: 2 profiles, 1 showing the work. Next week: put
more effort into the scheduler maintainers and drop the talk list.
