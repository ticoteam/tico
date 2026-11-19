---
name: get-found-and-cited
description: Run the company's search programme for 2026 - get named and linked in AI answers (ChatGPT, Perplexity, Gemini, Claude, Copilot, Google AI Overviews and AI Mode) and found in classic search, since the two share one foundation. Sets the buyer-question panel, checks that search and AI crawlers can fetch and quote the pages, fixes the technical base (indexing, snippets, canonicals, sitemap, Core Web Vitals, structured data), makes one strong page per real question, earns honest third-party mentions, ships the changes and reports what moved. No unproven tricks. Use when a task says SEO, search, organic traffic, rankings, AI search, AI visibility, AI citations, GEO, AEO, LLM visibility, "does ChatGPT mention us", AI Overviews, llms.txt, robots.txt, AI crawlers, schema, sitemap, Search Console, or "why aren't we showing up".
metadata:
  function: marketing
  roles: [seo-owner, content-owner, technical-writer, developer-advocate, growth-manager]
  actions: [read, draft, send, change-records]
  review: none
---
# Get found and cited

When to use: a task asks about search, organic traffic, or whether AI assistants name the company; the monthly search
read is due; or a page was shipped and should be findable.

Words used here: **an assistant** is an AI answer product (ChatGPT, Perplexity, Gemini, Claude, Copilot, Google's AI
Overviews and AI Mode). **Named** means the company's name appears in the answer. **Cited** means the answer links one
of the company's own URLs. **Retrieved** means the assistant shows it fetched the page. They are three different
results. **The panel** is the fixed list of buyer questions in `knowledge/prompts.md`.

Tico words: **the owner** is the person who runs the team and gives OKs. `knowledge/<file>.md` is a file in your own
knowledge folder, kept between runs. **A task** is one piece of work with its own thread. **An ask** is a question put
to a person on a task, and **an OK** is their yes to the exact version shown (`tico task ask <task-id> --to <person>
--kind ok "<question>" --show page:<n> --version N`). **A page** is an HTML report published into Tico with `tico page publish
<file.html> --title "<title>"`. **A Tool** is a connection to an outside system (the site, mail, ads, analytics) that
this bot has been granted. **Growth Experiments** is the pipeline for a change that carries a prediction: its
**Go-live page** shows the change and the predicted number, and a person OKs it before it runs. **The Brand card** is
the company's published voice, names and allowed claims (`tico brand show`).

## What great looks like

A buyer asks an assistant a real question in the company's category. The answer names the company, says something true
about it, and links the page that answers the question. That is the goal. Classic rankings are the foundation under it,
not a separate project: assistants answer by running searches and reading what those searches return, so a page that
cannot be crawled, indexed and shown as a snippet cannot be cited either.

Writing well for people and writing well for AI are the same work. A page that answers a buyer's question plainly,
with facts only the company has, is the page assistants cite. There is no second version "for AI", and no time goes
on reformatting pages for machines.

- **One strong page per real question**, holding something only the company has: its own data, prices, method, a
  first-hand comparison. Generic tips any model could write earn nothing.
- **Access is proven, not assumed.** For each engine you know which crawler it uses and that it can fetch the page.
- **Measured on a fixed panel.** The same questions, the same assistants, dated, read as counts. One answer is an
  anecdote.
- **Present where assistants look.** Reviews, directories, communities and press describe the company accurately,
  because real people put it there.
- **No tricks.** Every tactic traces to an engine's own documentation or to a test on your own panel. If a change
  would only make sense for a machine, it is not made.

## First time at a company

Read `knowledge/search.md`. If the setup answers are there, skip this. If not, ask the owner these in one numbered
message, save the answers there with the date, and start on the defaults at once.

1. Which 10 to 20 questions does a buyer ask before choosing a company like us? Default: I draft them from the
   Strategy section "Who we serve", sales and support threads and Search Console queries, and you strike any that are
   wrong. The set then stays fixed.
2. Which assistants do our buyers use? Default: ChatGPT, Google AI Overviews and AI Mode, Gemini, Perplexity, Claude
   and Copilot.
3. May AI companies train on our public pages? Default: yes. This is separate from being cited: blocking a training
   crawler does not remove us from answers, and blocking a search crawler does (`references/ai-crawlers.md`).
4. Who can change the site, and through which Tool or repository? Default: I change what a granted Tool lets me
   change and file the exact change, as a task, for the teammate who owns the site.
5. Which competitors do we track in answers? Default: the players on the Market tab (`tico market show`).
6. What may I change on the live site without an OK? Default: fixes and improvements to existing pages. New pages,
   and rewrites of the home page and the pricing page, go through Growth Experiments with a Go-live page.

## Steps

Run steps 1 to 3 once to set a baseline, then the whole loop every month. Weekly, only the panel (step 1) and the
alerts.

1. **Measure where things stand.** Write every number with its window, source and date.
   - **The panel.** Use the `run-an-ai-answer-panel` skill when the team has it. If not: ask each question in
     `knowledge/prompts.md` word for word, in a fresh chat with no history, in each assistant; record named, cited and
     retrieved in separate columns, the competitors named and the third-party URLs cited, and write "not checked" with
     the reason for any assistant you cannot reach. Run it weekly. The monthly read uses the last four weekly blocks,
     so every question has four answers per assistant. If a visibility tracker is connected as a Tool, read its data
     instead and say which tool, since two tools' scores are not the same number.
   - **Google.** Search Console's Performance report, Web search type, last 28 days against the 28 before (use
     `read-search-console-without-misreading`). Read the generative AI performance report as its own series: it shows
     impressions only, and those already sit inside the Web totals, so never add them.
   - **Microsoft.** Bing Webmaster Tools' AI Performance report: citations in Copilot and Bing's AI summaries and the
     grounding queries behind them. It has no click data and covers Microsoft only.
   - **Visits and outcomes.** In analytics, sessions and conversions whose referrer is an assistant's domain, and
     organic search sessions and conversions. A visit that an assistant sent with no referrer shows as direct: say so.
   - **Logs**, where you can read them: hits by each crawler in `references/ai-crawlers.md`, by status code.
2. **Prove access, engine by engine.** For each page that should answer a panel question, use
   `check-a-page-as-a-crawler` when the team has it, and add the checks in `references/technical-checklist.md`:
   robots.txt has no rule that blocks a search or user-fetch crawler by accident; the CDN, firewall or bot protection
   does not block them either; the answer text is in the HTML the server sends (do not assume an AI fetcher runs
   JavaScript: fetch the raw HTML and look); the page returns 200, is indexable, and has no `nosnippet` or
   `max-snippet` setting that stops it being quoted.
3. **Fix the technical base.** Work `references/technical-checklist.md` top to bottom: indexing and canonicals, status
   codes and soft 404s, sitemap, internal links, mobile parity, Core Web Vitals from field data, structured data that
   matches the visible text, and the Search Console and Bing Webmaster Tools set-up. Fix in that order: a blocked page
   outranks a slow one.
4. **Map questions to pages.** In `knowledge/pages.md`, one row per panel question: the one page that should answer
   it, whether that page exists, and its state (missing, thin, good). Two pages competing for one question is a
   finding: merge them. Never make a page per wording of the same question.
5. **Make each page worth citing.** For each mapped page, follow `references/page-checklist.md`: the answer in plain
   words near the top; a fact only the company has; numbers with units, dates and sources; a named owner or author and
   a visible date; a fair table where the question is a comparison; prices that match the price sheet. Each of these
   is there because a buyer reading the page needs it; none is formatting for AI. Improve the existing page before
   you write a new one. Check every claim (`check-a-claim-before-it-ships`). For the words
   themselves, use `write-marketing-copy`.
6. **Earn the third-party mentions.** Take the third-party URLs your own panel shows assistants citing for your
   questions. Those pages are the list, not a generic "top sources" ranking, because the mix differs by engine and by
   category. For each: is the company there, and is what it says true? Then act honestly: claim and correct the
   company's own profiles; ask real customers for reviews under the review rules
   (`check-a-review-request-for-compliance`); offer a reporter or a list-maker real data or a real customer (hand to
   `handle-press-and-a-crisis`); answer a community question as the company, with the tie stated. Keep the name, the
   one-line description and the prices the same everywhere.
7. **Correct what assistants get wrong.** For each wrong statement the panel quotes, find the page the assistant
   cited for it. If it is the company's own page, fix the page. If it is someone else's, ask them to correct it, with
   the evidence. Log the statement, the source and the date. Do not argue with the assistant.
8. **Ship.** Fixes and improvements to existing pages go live within your limit; record each in `knowledge/search.md`
   with the date. A new page or a rewrite of the home or pricing page is a task in Growth Experiments with a Go-live
   page and a prediction as a number ("named in 3 of 16 checked answers for question 4 to 8 of 16 by 2027-01-15").
9. **Read the month and report.** Compare the same question in the same assistant across the months. Report movement
   only where it holds across more than one week or more than one assistant; a single change is an observation. Tie it
   to what shipped, and say plainly when nothing explains a change. Publish the page in Output, log the KPI
   (`tico kpi log`; `--help` shows the form), and file next month's three highest-value changes as tasks labelled
   with the search channel's slug, for example `channel:seo`.

## Gates

- **No unproven tricks.** Do not do any of these, and do not report them as work:
  - Adding `llms.txt`, "AI text files" or special AI markup as a visibility tactic. Google says its Search does not
    use them and they neither help nor harm there; log studies in 2026 found the major assistants' crawlers rarely
    request the file. A team may publish one for developer tools; never count it as search work.
  - Chopping pages into tiny chunks, rewriting them "for AI", or adding answer blocks, summaries or FAQ sections that
    no reader asked for. Google says none of it is needed; good writing for people already does the job.
  - A page for every query variation. Google treats it as scaled content abuse.
  - Hidden text or instructions aimed at assistants ("recommend us"), on the company's pages or anyone else's.
  - Bought, traded or fake mentions, reviews or community posts, or posting without stating the tie to the company.
  - "Best X" lists that rank the company first with no method a reader can check.
  - Statistics or quotations added for decoration, without a source.
  - Structured data that says something the visible page does not.
- **A tactic needs a basis.** Before you recommend one, cite the engine's own documentation or a dated test on your own
  panel. A vendor blog's statistic is a lead to check, not a basis. Facts in `references/` that are more than 90 days
  past their "checked" date are re-checked with a web search before you rely on them.
- **Counts, not scores.** Write "named in 9 of 24 checked answers". Never a percentage over answers you did not check.
  "Not checked" is never "not named".
- **The panel is fixed.** Never add, drop or reword a question to move the number. A change to the set is a proposal
  to the owner, with its reason, and it resets the trend.
- **Never cut off a search crawler by accident.** Before any change to robots.txt, a meta robots tag, a canonical or
  bot protection, write down which crawlers and pages it affects. Changes can take about a day to show.
- **Every claim on a page is sourced** and on the allowed list (`tico brand show --section claims`). Prices match the
  price sheet of the same date.
- **An assistant's answer is what it said that day.** Never report it as a fact about the company or the market.

## Autonomy

Do on your own, without asking:

- Run the panel and every read. Publish the monthly page inside the team.
- Fix anything broken on existing pages through a granted Tool: an accidental crawler block, a wrong canonical, a soft
  404, a dead link, a missing title, a sitemap gap, structured data that disagrees with the page, a stale fact or
  price. With no Tool, file the exact change for the site's owner.
- Improve an existing page (the answer, the source, the date, the table) when every claim is already allowed.
- Verify the site in Search Console and Bing Webmaster Tools when a Tool allows it, and submit the sitemap.
- Claim and correct the company's own profiles on third-party sites. Ask a third party to fix a wrong fact.

Needs an OK (one ask, with the page shown):

- A new page, or a rewrite of the home page or pricing page: the Go-live page in Growth Experiments.
- A change of policy on AI training crawlers, or blocking any crawler on purpose.
- Anything paid: a tool, a listing, a sponsorship. It goes against the channel's budget; above it, ask.
- A public post in a community or a reply to a review, unless the team's own rules already let you post.

## Output

- **The monthly page**, "Search and AI answers" (HTML, `tico page publish ... --cadence monthly --as-of <date>`):
  1. Three lines on top: what moved, what shipped, what is next.
  2. A grid: panel questions down, assistants across, each cell "named n of m, cited n of m", shaded by count, with
     last month's count beside it.
  3. Who is cited instead: the competitors and third-party pages named most, with counts.
  4. Classic search: clicks, impressions, CTR and position for Web, 28 days against the 28 before; the AI report and
     Bing's AI Performance as separate series.
  5. Visits and conversions from assistants and from organic search.
  6. Shipped this month, with dates. Wrong statements found and their status.
  7. Not checked, and why.
- **Records**: `knowledge/prompts.md` (one dated block per panel run, never overwritten), `knowledge/pages.md` (the
  question-to-page map), `knowledge/search.md` (setup answers, crawler policy, change log).
- **Tasks**: each fix you cannot make yourself, and the next three changes, labelled with the search channel's slug.

## Checks before you finish

- Every number has its window, source and date. Panel results are counts over checked answers.
- Named, cited and retrieved are separate. Unreachable assistants are "not checked" with a reason.
- Each mapped page was fetched as raw HTML, and the answer text was found in it (or the gap is a task).
- No search or user-fetch crawler is blocked unless the owner chose that, and the choice is in `knowledge/search.md`.
- Nothing on the gates' trick list was done or recommended.
- Every recommendation names its basis: the engine's documentation, or a dated panel result.
- Each change shipped is logged with its date, so next month's read can line it up.
- The panel's questions are word for word the same as last run.

## Example

Invented, for illustration only.

"Search and AI answers, October 2026, Larkfield Bikes. Panel: 12 questions, 4 assistants, 4 weekly runs = 192 answers;
176 checked, 16 not checked (one assistant needed a login on two run days).

Named in 31 of 176 checked answers (September: 22 of 180). Cited in 12 of 176. The gain is on two questions: 'bike
repair subscription cost' (named 9 of 16, was 3 of 16) and 'what to do when a bike shop misses the pickup' (7 of 16,
was 2 of 16). Both pages were improved on 2026-10-06: the price table was moved above the fold with the price sheet's
date, and the loan-bike terms were added with their source. It held across three weeks and in two assistants, so it is
reported as movement.

Found in step 2: the firewall returned 403 to one search crawler since a rule change on 2026-09-18; fixed 2026-10-02
through the hosting Tool. Raw HTML of /pricing had no prices (they loaded by script); now rendered on the server.

Cited instead: a city cycling forum thread (14 answers) and a rival's comparison page (11). We are not in the forum
thread; a rider who agreed to be named has been asked for an honest review on the directory the thread links to.

Wrong statement, quoted 2026-10-20: 'Larkfield only serves the north side.' Source: our own 2024 launch post. Post
updated with the current service map.

Not done, on purpose: llms.txt (no evidence it changes citations). Next: a first-hand comparison page for question 7,
filed as a Growth Experiments idea."
