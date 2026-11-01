# Weekly docs drift report

Schedule: Wednesdays at 10:00 team time (routine `weekly-docs-drift`), after setup. Also run by hand. Budget 45 minutes.
No docs are changed.

---

## Inputs

- `knowledge/docs-map.md`, `knowledge/style.md`, `knowledge/glossary.md`, and last week's report.
- Merged pull requests since the last report, in the repositories the owner listed:
  `gh pr list -R <repo> --state merged --search "merged:>YYYY-MM-DD" --json number,title,body,files,url`.
- The docs in those repositories, as far as access reaches, at the current commit of the default branch.

## Steps

1. **Read where things stand.** `hub task show <id>`, then the inputs above.
2. **List the changes users can see.** From the pull requests, keep those that change what a user sees or types: a
   renamed option, a new step, a removed feature, a changed default, a new page or setting, a changed API field. Drop
   refactors and tests. Read the diff (`gh pr diff <n> -R <repo>`) when the description is vague.
3. **Find the pages each change affects.** Use the docs and `knowledge/docs-map.md`. For each affected page, decide:
   **wrong** (states something now false), **incomplete** (omits a new step or option), **missing** (no page covers
   it), or **fine**. Quote the wrong sentence and the pull request that made it wrong. If an internal doc or the help
   centre is also wrong, make one task for the Librarian, and do not draft it.
4. **Verify before you call it wrong.** Check each sentence with `skills/check-a-doc-against-the-code/SKILL.md`. A
   sentence you cannot confirm is "to verify", not "wrong".
5. **Rank.** (1) Wrong, on a page many readers use: setup, sign-in, billing, the API. (2) Missing pages for a feature
   already shipped. (3) Incomplete. Draft fixes for the top three, and list the rest in one line each.
6. **Draft the fixes.** Follow `playbooks/draft-a-doc-page.md`. Save each draft in
   `reports/drafts/YYYY-MM-DD-<page>.md` with the old text, the new text and the pull request as the reason.
7. **Write the report.** `reports/YYYY-MM-DD-docs-drift.md`, in the shape of `knowledge/examples/docs-drift-report.md`.
   Then `hub file publish reports/YYYY-MM-DD-docs-drift.md`.
8. **Finish.** Commit, then `hub task update <id> --status done --note` with the pages found, the drafts made and the
   unread sources. Commit requested drafts with the repository Tools, and run the required checks.

## Output

`reports/YYYY-MM-DD-docs-drift.md`:

- **Headline:** how many pages drifted, the worst one, and the pull request that caused it.
- **Drifted pages:** the page, its type, the wrong text, the cause (pull request and date), and the draft path.
- **Missing or incomplete:** one line each.
- **Fine:** how many pages were checked, against how many changes.
- **Could not read:** repositories, pages or internal docs not compared, with the reason.
- **Sources:** the pull requests and their date range, the repositories, and the date the docs were read.

## Done when

- Each change on the list is mapped to its pages, or to "no page affected" with the reason.
- Every "wrong" finding has the quoted sentence, the pull request and a verification result.
- Every page you could not open is named under "Could not read". Never report "no drift" for it.
- The report is published and the task is closed.

## When a source fails

If the docs site or the repository cannot be read, say which pages were not compared. Never report "no drift" for a
page you could not open.
