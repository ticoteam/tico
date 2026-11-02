---
name: tidy-the-docs-sweep
description: "Sweep the team's docs for upkeep problems (duplicates, drafts left behind, docs with no owner, dead links, misplaced docs and text that looks like a credential), report each one, and change nothing in a person's doc."
---
# Tidy the docs sweep

When to use: on the Monday map refresh, after the map's own reconcile (`playbooks/refresh-the-map.md` step 4), and when a person asks you to tidy the docs.

Steps:
1. Work from the full inventory: `hub doc list --limit 500` and every `next_cursor` after it, paged as in `playbooks/refresh-the-map.md` step 1. Group the titles by folder.
2. Duplicates: two docs whose titles match once case, "copy", "(1)" and "old" are ignored, or that open with the same paragraph. Read both in full before you call them duplicates. Report both paths. Never delete, move or archive either.
3. Drafts left behind: a doc whose text still says TODO, TBD, draft or placeholder, and whose `updated` date is more than 90 days old. Report it with that date.
4. No owner: a doc that states a price, a policy or a process and names no owner in its text. Report it.
5. Dead links: an internal doc that points to an archived doc, or to a linked source that `_librarian/missing.md` lists as unreadable. Match the addresses against `hub doc link-list`. Report the link.
6. Misplaced: a doc whose folder does not match its title and text, such as a holiday schedule at the top level. Report it only when the right folder is plain from the doc itself.
7. Credentials: text that looks like a password, key or token. Never quote or copy it. Say the doc holds what looks like a credential and that someone should remove it (the `AGENT.md` rule), and list it in `_librarian/missing.md`.
8. Write the findings. Drafts and dead links go under "Out of date" in `_librarian/missing.md`. Duplicates, misplaced docs and docs with no owner go in one task for the team owner, titled "Tidy the docs". Open it only if `hub task list` shows no open task with that title, and otherwise update that task with `hub task update <id> --note`. Credential findings go in the task too, without the text.

Checks before you finish:
- Every finding names its doc path. A duplicate names both paths and the date each was read.
- The sweep edited, moved, archived and deleted nothing.
- No password, key or token text appears in any finding, the gap list or the task.
- At most one open "Tidy the docs" task exists after the sweep.

Example (invented, for illustration): A Monday sweep. "Pricing (copy)" and "Pricing" open with the same plan table, and both were read in full, so they are reported as duplicates. "Q2 planning DRAFT" was last changed 2026-06-03 and still says TODO, so it goes under Out of date. A holiday schedule sits at the top level, where the people folder is the plain home for it, so it is reported as misplaced. One open task "Tidy the docs" goes to the team owner with these three lines.
