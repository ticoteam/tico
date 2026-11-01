# Draft a doc page

Triggered by a task asking for a page or a fix, and used for each draft in `playbooks/weekly-docs-drift.md`. Budget 30
minutes. The outcome is one draft file in `reports/drafts/`: one reader, one type, one job, ready for the docs owner to
commit with one edit.

---

## Inputs

- The task, the reader from `state.md`, and the job in one sentence ("a new admin invites a teammate").
- The pull request or the page the draft fixes, with its diff and the current page text.
- `knowledge/style.md` and `knowledge/glossary.md`.

## Steps

1. **Decide who and what.** `hub task show <id>`. Write the reader and the job in one sentence. Choose exactly one type:
   - **Tutorial:** teaches by doing something safe, start to finish.
   - **How-to:** solves one task for someone who knows the basics. Use `skills/draft-a-how-to/SKILL.md`.
   - **Reference:** complete, dry facts in a fixed structure: options, fields, errors.
   - **Explanation:** why it works this way, and the context.
   If the request mixes two, draft the main one and link to a stub for the other.
2. **Gather the facts.** Read the pull request, the diff and the current page. Run each step against the code. If a step
   depends on a screen or behaviour you cannot confirm, mark it `[to verify: who can confirm]`. Use the exact names in
   `knowledge/glossary.md`.
3. **Write in the team's style.** Follow `knowledge/style.md` first. Where it is silent: second person, present tense,
   active voice, sentence-case headings, one action per numbered step, the expected result after the step, and no
   "simply" or "just".
   - How-to: the title is a goal ("Invite a teammate"), then when to use it, prerequisites, numbered steps, what you see
     when it worked, what to do if it did not, and links to related pages.
   - Reference: a table or list in a fixed order, with every option's type, default and an example.
   - Tutorial: a small working goal, with a checkpoint after each step.
   - Explanation: the concept, the reasoning and the trade-offs, without steps.
4. **Show the change.** For a fix, put the old text, the new text and the reason (pull request link, date) at the top of
   the draft. For a new page, put the reader, the type and the sources there. Keep the draft in the format of the docs
   repository.
5. **Check it.** Run `skills/check-a-doc-against-the-code/SKILL.md` on the draft. Every link points at a page that exists,
   or is marked new. No credential or customer detail appears. The page is short enough for a reader to finish in one
   sitting.
6. **Finish.** Save the draft, attach it to the task, commit, then `hub task update <id> --status done --note`: the path,
   the type, the items marked to verify, and who should review. Commit requested changes with the repository Tools, run
   the docs build and checks when they are available, and name any Tool that is missing.

## Output

One file, `reports/drafts/YYYY-MM-DD-<page>.md`, with three parts in order: the change block (old text, new text, reason),
then the draft in the docs repository's format, then the list of items marked to verify.

## Done when

- The draft has one type, one reader and one job.
- Every command, path, name and link is checked, or is marked to verify with the person who can confirm it.
- The task names the draft path, the type and who should review.

## When you cannot verify

Ask once with `hub task ask <id>`, naming the sentence and the human who knows. Ship the draft with the gap marked.
