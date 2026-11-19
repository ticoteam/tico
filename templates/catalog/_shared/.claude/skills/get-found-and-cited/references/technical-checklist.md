# Technical checklist

Work top to bottom. A higher row outranks a lower one: a page that cannot be fetched gains nothing from being fast.
For each row write pass, fail or not checked, with what you looked at and the date. Start with the pages mapped to
panel questions, then the templates behind the most pages.

## 1. Can it be fetched?

| Check | How | Fail means |
|---|---|---|
| Status | Fetch the URL as a plain request with no cookies | Anything but 200 on a page that should rank. A 200 that says "not found" is a soft 404. |
| Redirects | Follow the chain | More than one hop, a loop, or a redirect to the home page instead of the matching page |
| Crawler access | `ai-crawlers.md`, "Where blocks hide" | A search or user-fetch crawler gets 403, 429, a challenge or a disallow |
| Raw HTML | Fetch without running scripts and search for the page's key sentence, price or table | The answer only exists after JavaScript runs |
| HTTPS | Load the http and non-www forms | They do not all end at one https address |

## 2. Can it be indexed and quoted?

| Check | How | Fail means |
|---|---|---|
| Index state | Search Console URL Inspection, or the Page indexing report | "Excluded" for a reason nobody chose: noindex, duplicate, crawled but not indexed |
| Robots directives | Meta robots tag and `X-Robots-Tag` header | `noindex`, `nosnippet` or a `max-snippet` limit on a page that should be cited |
| Canonical | The `rel=canonical` in the raw HTML | Missing, pointing at another page, or different from the URL in the sitemap and internal links |
| Duplicates | Parameters, trailing slashes, upper and lower case, print views | Several addresses serve the same page with no canonical joining them |
| Bing | Bing Webmaster Tools: site verified, sitemap submitted, URL inspection | Not verified. Copilot's answers and ChatGPT's search lean on more than Google. |

## 3. Can it be found?

| Check | How | Fail means |
|---|---|---|
| Sitemap | Open the XML sitemap | Missing, not referenced in robots.txt, or listing URLs that redirect, 404 or are noindex. `lastmod` set to today on every URL is noise. |
| Internal links | From the home page, count clicks to the page; list pages with no inbound links | A page mapped to a panel question is more than three clicks deep or has no inbound link |
| Link text | Read the links pointing at the page | "Click here" and "learn more" instead of words that say what the page answers |
| Titles and descriptions | One `<title>` and one meta description per page | Missing, duplicated across pages, or not saying what the page answers |
| Headings | One `h1`; headings in order | The question the page answers is not in a heading |
| Languages | `hreflang` where the same page exists per language or country | Missing return links, or a wrong language code |

## 4. Does it load well for people?

Use field data (real visitors, 75th percentile over 28 days) from Search Console's Core Web Vitals report or the
Chrome UX Report. Lab scores from a single test are a way to find the cause, not the result.

| Metric | Good (checked 2026-11-18) |
|---|---|
| Largest Contentful Paint (LCP) | 2.5 seconds or less |
| Interaction to Next Paint (INP) | 200 milliseconds or less |
| Cumulative Layout Shift (CLS) | 0.1 or less |

- Check mobile first. Google indexes the mobile version: content missing on mobile is missing.
- No pop-up that covers the content on arrival.
- With too little traffic for field data, say "no field data", report the lab numbers as lab numbers, and move on.
  Speed is a tie-breaker, not a lever: do not spend a month on it while row 1 or 2 fails.

## 5. Structured data

Structured data helps search engines show rich results. No engine documents it as a requirement for AI answers, and
Google says no special schema is needed for them. So: use it where a rich result exists for the page type, and never
as an AI tactic.

- Use the type that matches the page: `Organization` on the home or about page, `Product` with `Offer` on product and
  pricing pages, `Article` with author and dates on articles, `LocalBusiness` for a place people visit,
  `BreadcrumbList` for the trail.
- **Everything in the markup is also visible on the page.** A price, rating, date or answer that appears only in the
  markup is a violation, not an optimisation.
- Ratings and reviews in markup are real, collected by the company's own process, and shown on the page.
- Test each template with the engine's rich results test and read Search Console's enhancement reports for errors.
- Check which rich results still exist before you add a type. Google has retired or narrowed several.

## 6. Listings that feed answers

- **Products**: a Merchant Center feed with prices and stock that match the site.
- **A local business**: a claimed Business Profile and Bing Places entry with the same name, address, phone and
  hours as the site.
- **The company's own profiles** on the review sites and directories its category uses: claimed, current, and worded
  the same as the Brand card's one-line description.

## 7. Measurement is set up

- Search Console and Bing Webmaster Tools are verified for the right property (the domain, not one protocol).
- Analytics separates organic search from assistant referrals. List the assistant domains you count in
  `knowledge/search.md`, so next month counts the same ones.
- Server or CDN logs are readable, or the gap is written down.

## Order of fixes

1. Anything in section 1 or 2 on a page mapped to a panel question.
2. The same faults on a template, because one fix repairs many pages.
3. Sections 3 and 6.
4. Section 5 errors.
5. Section 4, once the rest passes.

Each fix you make: the URL or template, what changed, the date, and how you confirmed it (re-fetched, re-inspected).
Each fix you cannot make: a task for the site's owner with the exact change, the reason and the pages affected.
