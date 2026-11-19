# Page checklist: is this page worth citing?

One page, one question. Run this on each page mapped in `knowledge/pages.md`. Writing well for people and writing
well for AI are the same work, so everything here is about making the page better for a person. That is the test:
if a change would only make sense for a machine, do not make it.

## 1. The question

- Write the buyer's question at the top of your notes, in the buyer's words (from the panel, a sales call, a support
  thread or a search query).
- Run the question in two assistants and one search engine. Note what the current answers say, which pages they cite
  and what those pages have that this one does not. That gap is the brief.
- Check `knowledge/pages.md`: no other page of ours answers the same question. If one does, merge them and redirect.

## 2. The answer

| Check | Pass looks like |
|---|---|
| Answer first | The first screen answers the question in two or three plain sentences. A reader who stops there leaves with the answer. |
| Stands alone | The key sentences name their subject ("Larkfield's repair plan costs...", not "It costs..."), so a reader who skims or lands mid-page still knows what each one is about. |
| Specific | Numbers have units, dates and conditions: "from $19 a month, billed monthly, as of 2026-11-02". |
| Complete | The obvious follow-up questions (how much, how long, what if, compared with what) are answered on the page, under headings that say so. |
| Honest limits | The page says who the product is not for, or when the answer does not apply. |

## 3. Something only we have

A page that repeats what every other page says gives an assistant no reason to cite it. Each page carries at least
one of these, and you can name which:

- Our own data: counted from our records, with the count, the window and the date.
- Our own prices, terms, limits or service area, matching the current price sheet.
- Our own method: how we do the thing, step by step, with the reasons.
- First-hand experience: we tested it, measured it, photographed it. Say how.
- A fair comparison we are in a position to make, with the source and date for every row about someone else.
- A named expert's view, with the person's name and role and their permission.

If the page has none, do not polish it. Either find the real material (ask the teammate who has it) or drop the page
from the map and say so.

## 4. Proof and provenance

- Every number, ranking and "only" or "first" has a source a reader can open, beside the claim. Our own numbers say
  how they were counted.
- Every claim is on the allowed list (`tico brand show --section claims`). Run `check-a-claim-before-it-ships` when
  the team has it.
- A visible author or owner, with a line on why they know. A visible "updated" date that changes only when the
  content does.
- Customer quotes are real, attributed, and used with permission. Never write one.
- Statistics and quotations are there because the reader needs them. Adding them as decoration to look citable is on
  the skill's trick list.

## 5. Comparisons and "best" pages

- State the method at the top: what was compared, how, when, and that the page is ours.
- Every row about a competitor has a dated source. Say what the competitor does better.
- Never rank the company first on a list the company wrote without a method a reader can check. Prefer "X compared
  with Y" pages, which can be fair, to "best X" pages, which rarely are.
- A named-competitor comparison needs an OK before it ships.

## 6. Form

- One `h1` that states the question or its answer. Headings a reader could use as a table of contents.
- A table where the content is a comparison, a price list or a set of specs. Numbered steps where order matters.
- Short paragraphs. Plain words. The buyer's terms, not internal ones. Define a term the first time it appears.
- Images have alt text that says what they show. Nothing important exists only inside an image or a video: put the
  price table and the key facts in text.
- No need to chop the page into fragments, add a question-and-answer block for its own sake, or write a summary "for
  AI". Google says none of this is required. Add a short FAQ only when real customers ask those questions.

## 7. Connections

- The page links to the next thing the reader needs (pricing, sign-up, a deeper guide), and at least two related
  pages link to it with descriptive text.
- Name, one-line description and prices match the Brand card, the price sheet and the company's profiles elsewhere.
- Title and meta description say what the page answers. They are written for the person scanning results.

## 8. After it ships

- Re-fetch the raw HTML and find the answer sentence in it.
- Request indexing in Search Console and Bing Webmaster Tools when a Tool allows it. Check the URL is in the sitemap.
- Log the change and its date in `knowledge/search.md`. Give it four weekly panel runs before you read its effect.
- Set a review date: three months for prices and comparisons, six for the rest. A stale date on a page is worse than
  none.

## What a finished page record looks like

```
Question 4: "bike repair subscription cost"        Page: /pricing        State: good (was thin)
Only we have: our price table and what each plan includes; loan-bike terms.
Changed 2026-10-06: answer moved to first screen; price sheet date shown; loan-bike terms added with source.
Claims: A1 (same-day repairs, proof: ops log Q3), A3 (loan bike). Both allowed.
Raw HTML: answer sentence found. Indexed: yes, inspected 2026-10-07.
Review by: 2027-01-06.
```
