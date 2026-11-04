# Visibility method

The standing method behind `playbooks/weekly-visibility-report.md`. `prompts.md` holds the questions and `pages.md`
the pages. This file holds what is true about how search and AI answers work, so each run does not re-derive it. Every
figure carries its source and the year it was read. Check a figure at its source before you quote it in a report.

## What an AI answer needs from a page

- Google's guidance on AI features (Search Central, read 2026): a page must be indexed and eligible to show with a
  snippet to appear in AI Overviews or AI Mode. There are no additional requirements. No new machine-readable AI files
  and no special schema.org markup are needed. Structured data should match the visible text.
- Snippet controls carry into those features as well as into ordinary snippets. `noindex` drops the page from Search.
  `nosnippet` stops text snippets for the whole page, `max-snippet:<n>` caps snippet length at n characters, and
  `data-nosnippet` on one element keeps only that part out of snippets. Read which control is set and its scope, and check
  the values at Google's robots meta page, before you call a page excluded. Do not recommend them on a page the team wants
  cited.
- `Google-Extended` controls AI training and grounding in some of Google's other systems. It is not the switch for
  Search. Read the Search Central page before you say what it does.
- Other engines differ. Measure each one separately, and do not assume one engine's source mix holds for another.

## What research says about rewriting pages

- Aggarwal et al., "GEO: Generative Engine Optimization" (KDD 2024, arXiv 2311.09735). The abstract reports that its
  methods could raise visibility in generative-engine responses by up to 40% on its benchmark, and that effectiveness
  varies by domain. The gains were measured in that setting, not in today's live engines.
- Puerto et al., "C-SEO Bench: Does Conversational SEO Work?" (NeurIPS Datasets and Benchmarks 2025, arXiv 2506.11097).
  The abstract finds that most conversational-SEO rewrites were largely ineffective and often lowered a document's
  ranking, that traditional SEO factors worked better, and that gains shrink as more sites adopt the same tactic.
- Practical reading: a page earns a citation mainly by being retrieved for the question and holding a clear, dated,
  sourced passage that answers it. Statistics or quotations added for decoration, without a source, do not.

## Search Console

- The Performance report's "Web" search type includes AI Overviews and AI Mode traffic (Search Central, read 2026).
- A separate Search generative AI performance report launched in June 2026 (Search Central Blog, June 2026). Secondary
  write-ups describe it as impressions by page, country, device and date, and say those impressions already sit inside
  the Web totals. Read the report's own definitions in Search Console before you quote it, and never add it to Web totals.
- Anonymized queries (too few users to show) are left out of query tables and query filters, but their clicks stay in
  the chart total (general Search Console behavior; the help page itself was not read for this file, so check it before quoting). Summed query rows will not
  match the total. A missing query is unknown, not zero.

## Measuring AI answers

- Three results, three columns: named (the team's name appears in the text), cited (a link to one of the team's own URLs
  appears), and retrieved (the tool shows the engine fetched the page). A brand can be named without a link, and a page
  can be retrieved and not cited.
- A fixed panel: the same questions, the same wording, the same assistants, dated. One answer is an anecdote. The trend
  across several weeks is the finding.
- A panel score is the share of checked answers that name the brand. It is not demand, share of market or sales, and two
  tools' scores are not the same number.
- An assistant's answer changes from day to day. A quote is what it said that day.

## Pages

- A missing URL must return 404 or 410. One that returns 200 with an error or a generic app shell is a soft 404: crawlers
  keep fetching it and it is not a page. Team practice, checked on the pages you audit.
- A sitemap is a discovery hint. Read the whole sitemap, or every child sitemap, before you say a URL is missing from it.
  A partial fetch can miss it.
- Canonical and title must match in the HTML the server sends. If they are missing or differ there, check the rendered page or URL Inspection before you call them wrong, since a page built in JavaScript can still be indexed.

## Sources

- Google Search Central, "AI features and your website" (developers.google.com/search/docs/appearance/ai-features), read 2026-11-01.
- Google Search Central Blog, "Introducing Search Generative AI performance reports in Search Console" (June 2026). The article body was not read; the description comes from secondary write-ups dated 2026.
- Aggarwal et al., arXiv 2311.09735 (KDD 2024), abstract read 2026-11-01.
- Puerto et al., arXiv 2506.11097 (NeurIPS Datasets and Benchmarks 2025), abstract read 2026-11-01.
- Search Console Help on anonymized queries: not read for this file; check before quoting.
