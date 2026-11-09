# Page audit

Triggered by a task that names a page or a question ("why don't we show up for X?"), or by the weekly report choosing a
page. Budget 25 minutes.

Outcome: one note on the task that says what is wrong with the page for a buyer and for search, the drafted change in
`knowledge/drafts/`, and what could not be checked. Never edit the live page.

---

## 1. Read the request

    hub task show <id>

Note the buyer question the page is meant to answer, from `knowledge/pages.md`. If no question is written for the page,
ask the requester one question in the task rather than guessing.

## 2. Check the page as a crawler

Follow `skills/check-a-page-as-a-crawler/SKILL.md`. If the page does not load, or returns the wrong status, that is the
finding. Stop there and draft the fix.

## 3. Check the content against the question

Does the page answer the buyer question in full, on its first screen, with facts that have a dated source and a named
owner? Compare it with the two pages that rank or are cited above it for the same question. Read them and note what they
answer that this page does not. Do not copy their wording.

## 4. Check the claims

Read every number, price, date and claim word on the page against a dated source the team owns. A short claim keeps its
basis and its date: "per year, from the 2026 price list", not "from $X". Words such as "open source", "free", "certified"
or "guaranteed" stay only where a license, the terms or a certificate says so. Anything that cannot be sourced is a gap
for the owner. Do not fill it.

## 5. Draft the change

Write the exact new title (about 60 characters or fewer), description (one or two sentences), or heading or paragraph, in
the team's voice from `knowledge/company.md`. Save it as `knowledge/drafts/<page-slug>.md` with the page URL, the check
date, the expected effect, and the check that will show whether it worked, with the date to run it.

## 6. Finish

Update the page's line in `knowledge/pages.md`, commit, then:

    hub task update <id> --status done --note "..."

The note holds the finding in one line, the draft path, and what you could not check.
