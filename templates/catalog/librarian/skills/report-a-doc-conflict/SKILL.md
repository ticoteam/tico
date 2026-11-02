---
name: report-a-doc-conflict
description: "When two sources give different values for the same fact, use the replacement if one says it replaces the other; otherwise report both with their dates and owners without picking one, record the conflict once in the gap list, and open one task for a person if it matters."
---
# Report a doc conflict

When to use: two sources disagree on a figure, a date, a name, a step or a rule. You find it while you answer a question, refresh the map, sweep the docs, or read a linked page against a doc.

Steps:
1. Supersession first. If one source says it replaces the other ("replaces the 2025 version"), or a newer doc says so, this is not a conflict. Follow `skills/check-a-doc-before-you-cite-it/SKILL.md` step 6: name the replacement, quote and cite the line that says so, and list the old doc under "Out of date" in `_librarian/missing.md`. Answer with the replacement's value, dated and cited. The steps below are for a real conflict only.
2. Put the two claims side by side: the fact, each value quoted in its source's own words, each citation, and each source's date (a doc's `updated` date, or the walk date of a linked page) and owner.
3. Say what each source is, in one clause: a policy, a process, a web page, or a file a bot or person made. A file is evidence of what was made, not a decision. Do not rank the sources to choose a winner.
4. Answer with both values, dated and both cited. Begin by saying the docs disagree. Do not give either value as the answer.
5. Record it once under **Docs that disagree** in `_librarian/missing.md`: both links, both values and both dates, in the shape `playbooks/faq-and-gaps.md` gives. If the line is already there, leave it.
6. If a person will act on the fact (a price, a policy, a limit, a deadline), look for an open task on it first (`hub task list`). If there is none, file one: `hub task create --owner <person> --title "Settle <topic>: two docs disagree" --body "<both links and one sentence>"`. The person is the owner the docs name, or the team owner if none is named. One task per problem.

Checks before you finish:
- An explicit replacement was answered with the replacement, and the old doc is under Out of date, not in the conflict list.
- Both values are quoted in their source's words, each with a citation and a date.
- Neither value is presented as the answer.
- The conflict appears once in the gap list, with both links.
- At most one open task covers this problem, and it names both links.

Example (invented, for illustration): The pricing doc (updated 2025-11-02, no owner named) says a trial lasts 14 days. The help page (read 2026-08-02) says 30 days. The pricing doc does not say it was replaced. The answer: "The docs disagree on trial length: 14 days in the pricing doc, updated 2025-11-02, and 30 days on the help page, read 2026-08-02." The conflict goes under Docs that disagree, and one task goes to the team owner, since the pricing doc names no owner.
