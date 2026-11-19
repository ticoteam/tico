# Search and AI crawlers: who fetches what

Checked 2026-11-18 against each company's own documentation. Names and behaviour change. If today is more than 90 days
past that date, open the pages in `sources.md` and re-check before you write or change a rule.

Each company runs up to three kinds of crawler. Know which kind a rule touches before you write it.

- **Search crawler**: builds the index the assistant answers from. Block it and the company's pages stop being shown
  or cited in that assistant's answers.
- **User fetch**: fetches one page because a person asked the assistant about it or pasted its link. It may not follow
  robots.txt, because a person started the request.
- **Training crawler**: collects text to train models. Blocking it does not remove the company from answers.

| Company | Token in robots.txt | Kind | What blocking it does |
|---|---|---|---|
| OpenAI | `OAI-SearchBot` | Search | Pages are not shown in ChatGPT search answers. The site can still appear as a plain link. |
| OpenAI | `ChatGPT-User` | User fetch | OpenAI says robots.txt rules may not apply. Not used to decide who appears in search. |
| OpenAI | `GPTBot` | Training | Text is not used for training. No effect on search answers. |
| OpenAI | `OAI-AdsBot` | Ad checks | Checks the landing pages of ads placed with OpenAI. |
| Anthropic | `Claude-SearchBot` | Search | Pages are less likely to appear in Claude's search results. |
| Anthropic | `Claude-User` | User fetch | Claude cannot fetch the page when a person asks it to. |
| Anthropic | `ClaudeBot` | Training | Text is not used for training. |
| Perplexity | `PerplexityBot` | Search | Pages are not surfaced or linked in Perplexity. It is not a training crawler. |
| Perplexity | `Perplexity-User` | User fetch | Perplexity says it generally ignores robots.txt, since a person asked. |
| Google | `Googlebot` | Search | Removes the page from Google Search, and so from AI Overviews and AI Mode. |
| Google | `Google-Extended` | Training and Gemini grounding | Not the Search switch. Blocking it does not remove a page from AI Overviews or AI Mode. |
| Microsoft | `Bingbot` | Search | Removes the page from Bing, which Copilot's answers draw on. |

Notes that change what you do:

- **Google has no separate AI crawler for Search.** A page shows in AI Overviews and AI Mode when it is indexed and
  allowed to show a snippet. To limit what is quoted, use `nosnippet`, `data-nosnippet`, `max-snippet` or `noindex`.
  Search Console also has a control to opt a site out of generative AI features. Each of these costs visibility:
  change one only on the owner's decision.
- **Changes take time.** OpenAI and Perplexity both say a robots.txt change can take about 24 hours to take effect.
- **Anthropic** honours robots.txt and `Crawl-delay`, reads robots.txt per subdomain, and publishes no IP ranges, so
  do not try to allow it by IP. From 2026-10-30 it also recognises a machine-readable signal for AI-training
  preferences. Read the live article for the exact form before you rely on it.
- **Other crawlers exist** (Apple, Meta, Amazon, Common Crawl and more). Look each up in its owner's documentation
  before you write a rule for it. Do not copy a block list from a blog post.
- **A user agent can be faked.** Where the company publishes IP ranges (OpenAI and Perplexity do), verify a suspicious
  hit against them before you conclude a crawler misbehaved.

## The default policy

Unless the owner chose otherwise in `knowledge/search.md`:

1. Allow every search crawler and every user-fetch crawler in the table.
2. Allow training crawlers. If the owner opts out of training, block only the training tokens (`GPTBot`, `ClaudeBot`,
   `Google-Extended`) and leave the rest alone.
3. Keep private areas (accounts, carts, internal search results, staging) disallowed for everyone, as for any crawler.

A robots.txt that follows the default needs no AI-specific lines at all. Add a named group only to make an exception.
An opt-out of training looks like this:

```
User-agent: GPTBot
User-agent: ClaudeBot
User-agent: Google-Extended
Disallow: /
```

## Where blocks hide

robots.txt is the least common cause. Check, in this order:

1. **The CDN or firewall's bot settings.** Many have a "block AI bots" switch, and some turn it on by default. Read
   the setting, then confirm with the logs: a 403, a 429 or a challenge page served to a search crawler is a block.
2. **Rate limits and bot challenges** that return a page of JavaScript instead of the content.
3. **robots.txt**: a `Disallow: /` under `User-agent: *` with no group for the crawlers you want, or a named group
   left over from an old decision.
4. **Meta robots or `X-Robots-Tag` headers**: `noindex`, `nosnippet`, `max-snippet:0`.
5. **Rendering**: the answer is only there after scripts run. Fetch the raw HTML and search it for the sentence.
6. **Login, cookie or region walls** in front of the content.

Record what you found, and each change, in `knowledge/search.md` with the date.
