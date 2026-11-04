# Weekly search and AI visibility report

Schedule: Mondays at 08:00 team time (routine `weekly-visibility-report`), after setup. Also run when a task asks.
Budget 45 minutes.

Inputs: `knowledge/prompts.md`, `knowledge/pages.md`, `knowledge/competitors.md`, last week's report, and any
search-performance or AI-visibility export a person attached to the task.

Outcome: one report in the shape of `knowledge/examples/visibility-report.md`: the headline, the tracked questions
by assistant, search movement or an explicit "no search data" line, three fixes with drafts, technical issues on the
pages checked, and what could not be read. Nothing on the site changes.

Done when every tracked question has a result or a "not checked" with its reason, each of the three fixes has its draft
file, the report is published, and the task note carries the headline.

---

## 1. Read where things stand

    hub task show <id>

Read last week's report, `knowledge/prompts.md`, `knowledge/pages.md` and `knowledge/competitors.md`. For each change
last week proposed, note whether it was done, slipped or dropped.

## 2. Check the tracked questions in the assistants

Follow `skills/run-an-ai-answer-panel/SKILL.md` for every question in `knowledge/prompts.md`, in each assistant the owner
uses. Record for each answer: named or not, the wording, the competitors named, the URLs cited, and the date. If an
assistant cannot be reached, record that and go on. "Not checked" is never recorded as "not named".

## 3. Read search performance

If a search-performance export is attached or connected, follow `skills/read-search-console-without-misreading/SKILL.md`
for the pages in `knowledge/pages.md`, and for the queries that gained or lost most. If not, the report says "no search
data this week" in its search section, and uses public checks only. Never estimate a click.

## 4. Check the pages that matter

For the two or three pages in `knowledge/pages.md` with the oldest last check, follow
`skills/check-a-page-as-a-crawler/SKILL.md`. Any page that fails, or does not answer its buyer question, goes through
`playbooks/page-audit.md`.

## 5. Choose three fixes

Rank what you found by how much it would change a buyer's answer or a search result, and by how little a person must do
to apply it. For each fix record:
- the page (exact URL);
- the problem and its evidence: the question and date, the quote, or the check;
- the change: the exact new title, description, heading or paragraph;
- why it should help;
- the check that will show whether it worked, and the date to run it.

Save each draft in `knowledge/drafts/`. A content gap is written as a brief in the same folder. Create a task for another
bot only when the requester asks for one.

## 6. Write the report and publish it

Write `reports/YYYY-MM-DD-visibility.md` in the shape of `knowledge/examples/visibility-report.md`:
- headline: one sentence with the result, the count and the window;
- AI answers: questions by assistant, with named, cited and not checked shown separately;
- search: movement with its window, or the "no search data" line;
- three fixes, each with its draft path;
- technical: what the page checks found;
- could not read: each failed source, and what that leaves unknown;
- sources: each check with its date.

Then:

    hub file publish reports/YYYY-MM-DD-visibility.md

Add the dated checks to `knowledge/prompts.md` and `knowledge/pages.md`, rewrite `state.md`, and commit this repository.

## 7. Close

    hub task update <id> --status done --note "..."

The note holds the headline, the report path, and what you could not read. Finish the task even on a quiet week: an open
routine task gets absorbed into the next run.

## When a source fails

Name it in the report and in the closing note: which assistant, page or export failed, and what that leaves unknown. A
blocked source is never "no change".
