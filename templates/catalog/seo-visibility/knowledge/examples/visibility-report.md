A sample of excellent output for a fictional team. Every name, URL and number is invented.

```markdown
# Acme search and AI visibility, Mon 2026-09-28

Sample output for Acme, a fictional studio-software team. Nothing on the website was changed.

**Headline: Acme is named in 5 of 10 checked answers, up from 4 last week, and the pricing page is the largest missed
chance. One week is an observation, not a trend.**

## AI answers (checked 2026-09-28, two assistants, five questions)

| Question | Assistant A | Assistant B | Competitors named | Acme URL cited |
|---|---|---|---|---|
| Best booking software for small yoga studios | named | named | Rivalbook, Slotwise | acme.example/studios (A, B) |
| How do I reduce no-shows at my studio? | not named | not named | Rivalbook (both) | none |
| Pricing for studio booking software | named | not named | Rivalbook | acme.example/pricing (A) |
| Is there a free plan for studio software? | not named | not named | Slotwise | none |
| Studio software for two locations | named | named | Rivalbook | acme.example/multi-location (B) |

- Last week: 4 of 10. The same five questions, the same two assistants.
- Quote, assistant A, no-shows question, 2026-09-28: "Studios often use reminder texts..." The answer names no Acme page.
- Not checked: none.

## Search (Search Console, Web, 28 days to 2026-09-27, against the 28 days before)

- acme.example/pricing: clicks 41 (from 36), impressions 3,100 (from 2,900), average position 11.4 (from 12.1).
- Query gaining: "studio booking software price", impressions 360 (from 260).
- The query table does not add up to the chart total. Low-volume queries are hidden. They are unknown, not zero.
- The AI-features report was read as a separate series and is not added to these totals.

## Three fixes, drafted (nothing applied)

1. **Pricing page title and description.** Problem: the title reads "Pricing" only, and the page sits at position 11 for a
   query with rising impressions (export 2026-09-28). Change: title "Acme pricing for studios of 1 to 20 instructors";
   description in the draft. Draft: `knowledge/drafts/pricing-title.md`. Check: rerun the same export on 2026-10-26 and
   compare the same URL.
2. **No-shows answer.** Problem: no Acme page answers "how to reduce no-shows", and both checked assistants name a
   competitor for it. Change: a new page, written as a brief. The brief needs one customer result the team can source;
   none was supplied, so the brief marks it as a gap. Draft: `knowledge/drafts/no-shows-brief.md`.
3. **Studios page links.** Problem: the studios page does not link to the demo page, so the answer that cites it gives a
   reader no route to a demo. Change: two internal links, with the exact anchor text. Draft: `knowledge/drafts/studios-links.md`.

## Technical (pages checked as a crawler, 2026-09-28)

- /studios and /pricing: first status 200, own title, canonical points to itself, one H1, both in the sitemap.
- /old-plan returns 404 and is still listed in the sitemap. /blog/tag/news returns 404 and is still listed. Two of
  34 sitemap URLs are dead. Sitemap read in full (two child files).

## Could not read

- No account is connected for assistant C. Its answers are not checked this week.
- Search Console data ends on 2026-09-27. The last day is not in this report.

## Sources

- Tracked questions in `knowledge/prompts.md`, asked 2026-09-28 in assistants A and B.
- Search Console export, Web, pulled 2026-09-28.
- Pages /studios, /pricing, /old-plan and /blog/tag/news checked as a crawler, 2026-09-28.
```
