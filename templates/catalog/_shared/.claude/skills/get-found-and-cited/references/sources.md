# Sources

Read 2026-11-18 unless a line says otherwise. All text in this skill is original. This field changes fast: anything
below that is more than 90 days old should be opened again before you rely on it.

## Engine documentation (the basis for the rules)

- Google Search Central, "AI features and your website" (developers.google.com/search/docs/appearance/ai-features).
  A page needs to be indexed and eligible for a snippet; there are no extra requirements and no special optimisations
  for AI Overviews or AI Mode; the snippet controls; `Google-Extended` does not control Search; AI traffic is counted
  in the Performance report under Web; query fan-out.
- Google Search Central, "Optimizing your website for generative AI features"
  (developers.google.com/search/docs/fundamentals/ai-optimization-guide), dated 2026-05-15. "GEO" and "AEO" are still
  SEO; content should not be commodity content; Search does not use llms.txt, AI text files or special markup, and
  they neither harm nor help; no need to break content into small pieces or write specially for AI; no special
  schema; a page per query variation falls under scaled content abuse; inauthentic mentions help less than they seem
  to; Merchant Center and Business Profile for products and local; be wary of tools claiming internal Google metrics.
- Google Search Console generative AI performance report, launched 2026-06-03: impressions only (by page, country,
  device and date), no prompts; clicks stay in the main Performance report. Rollout date to all properties is from a
  secondary report: check in the product.
- OpenAI, "Overview of OpenAI crawlers" (developers.openai.com/api/docs/bots): `OAI-SearchBot`, `GPTBot`,
  `ChatGPT-User`, `OAI-AdsBot`, what each is for, and the 24-hour note.
- Anthropic, "Does Anthropic crawl data from the web, and how can site owners block the crawler?"
  (support.claude.com, article 8896518): `ClaudeBot`, `Claude-SearchBot`, `Claude-User`; robots.txt and `Crawl-delay`;
  no published IP ranges; the AI-training preference signal from 2026-10-30.
- Perplexity, "Perplexity crawlers" (docs.perplexity.ai/guides/bots): `PerplexityBot`, `Perplexity-User`.
- Microsoft, Bing Webmaster Tools AI Performance report (public preview announced February 2026): citations in
  Copilot and Bing's AI summaries, grounding queries, no click data.
- web.dev, Core Web Vitals thresholds (LCP 2.5 s, INP 200 ms, CLS 0.1, at the 75th percentile of field data).

## Research (leads, not rules)

- Aggarwal et al., "GEO: Generative Engine Optimization", KDD 2024 (arXiv 2311.09735). Reported visibility gains of
  up to 40% on its own benchmark from adding citations, quotations and statistics.
- Puerto et al., "C-SEO Bench", NeurIPS Datasets and Benchmarks 2025 (arXiv 2506.11097). Found most conversational-SEO
  rewriting methods ineffective. The two papers disagree, which is why this skill only adds a statistic or quotation
  when the reader needs it and it has a source.
- Ahrefs, May 2026, log study of 137,210 domains: 97% of llms.txt files received no requests. A vendor study, read
  from secondary coverage. SE Ranking's study of about 300,000 domains found no link between llms.txt and citation
  frequency (also a vendor study).
- Studies of which sites assistants cite most disagree by engine, by vendor and by month. This skill uses none of
  their percentages and tells you to read your own panel instead.

## Tico's own skills and files

- `templates/catalog/seo-owner/skills/run-an-ai-answer-panel`: the fixed prompt set, fresh chats, named, cited and
  retrieved as separate results, counts over checked answers, "not checked" is not "not named".
- `templates/catalog/seo-owner/skills/check-a-page-as-a-crawler` and `read-search-console-without-misreading`: used as
  sub-steps.
- `docs/growth.md` and `templates/catalog/_shared/playbooks/`: the Growth Experiments pipeline, the Go-live page with
  a prediction as a number, channel labels.

## Other skill libraries (ideas only)

- coreyhaines31, `marketingskills` (MIT): `seo-audit` and `ai-seo`. Ideas: audit in priority order with crawlability
  first; treat AI visibility as part of the same audit. Not taken: its advice to add llms.txt, which the engine
  documentation above does not support.
- AgriciDaniel, `claude-seo` (MIT). Ideas: check AI-crawler access and whether a passage can be quoted on its own as
  part of the page audit.
- Semrush, `skills`, `technical-seo-audit` (vendor-published; licence not confirmed, ideas only). Idea: Core Web
  Vitals read from field data at the 75th percentile, with lab data kept separate.
- Anthropic, `knowledge-work-plugins`, marketing `seo-audit` (Apache-2.0). Idea: findings ranked by impact with the
  fix beside each.

## Written new for this skill

- The rule that every tactic needs an engine document or a dated result on your own panel behind it, and the list of
  tactics that fail that rule.
- Building the off-site list from the URLs your own panel shows, instead of a generic ranking of sources.
- Correcting a wrong statement at the page the assistant cited for it.

## Check before quoting

- Crawler names and what blocking each does (`ai-crawlers.md`).
- Whether a rich result type still exists before adding its markup.
- Any percentage about AI search traffic or citation sources: most come from vendors and do not agree.
