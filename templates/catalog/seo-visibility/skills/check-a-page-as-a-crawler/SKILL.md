---
name: check-a-page-as-a-crawler
description: Check one public page the way a search crawler sees it (status code, raw HTML, indexing signals, sitemap entry and claims) before judging its copy or its ranking.
---
# Check a page as a crawler

When to use: before any page audit, after a page change to confirm it shipped, or when a page is said to be missing from
search or from AI answers.

Steps:
1. Request the exact URL without following redirects. Record the first status code and the final URL. A URL that should
   not exist must return 404 or 410. One that returns 200 with an error message or a generic app shell is a soft 404, and
   it counts as a broken page.
2. Read the raw HTML the server sent first. Record the `<title>`, the meta description, the canonical link, the H1, and any
   `noindex`, `nosnippet`, `max-snippet` or `data-nosnippet` setting with its scope. Check that the canonical points to this
   URL, or to the one you intend. If one of these is missing from the raw HTML, check the rendered page or Search Console's
   URL Inspection before you call it missing: a page built in JavaScript can still be indexed. Say which view you used.
3. Check that the body holds the page's own text. Confirm the words that identify the page appear in the raw HTML, or in the
   rendered page if the page builds its content with JavaScript. Say which.
4. Check robots.txt for a rule that blocks this path. Check the sitemap that should list the URL. Read the whole sitemap,
   or every child sitemap, before you say the URL is missing from it.
5. Check that at least one crawlable `<a href>` on another page of the same site links to this page.
6. Check each claim in the visible text against the team's dated sources. Words such as "open source", "free", "certified"
   and "guaranteed" need a license, terms or a certificate behind them.
7. Record one row: URL, first status, title, canonical, H1, indexable, in sitemap, claims checked, date. Mark anything you
   could not read.

Checks before you finish:
- The first status code is recorded, not the status after redirects.
- No page is called fine without its raw HTML having been read.
- The sitemap was read in full, or the gap is named.
- Each claim checked is matched to a named source.
- Nothing on the live site was changed.

Example (generic): "/pricing: first status 200; title and H1 present; canonical points to itself; in the sitemap. Claim
'from $X a month' matches the price sheet dated 2026-10-20. Could not read: structured data, which is not in the HTML."
