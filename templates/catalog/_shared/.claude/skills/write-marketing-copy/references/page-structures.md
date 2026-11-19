# Structures by piece

Each structure is an order that works, not a form to fill. Drop a section the piece has no real material for. Never
pad one to keep the shape. Every slot is filled from the spine in `knowledge/messaging.md`.

## Home page

**Job:** tell a stranger what the company does and send each kind of visitor to their next step. It serves several
readers, so it leads with the primary buyer and routes the rest.

| # | Section | What it holds | Test |
|---|---|---|---|
| 1 | Top | Headline (the promise), one or two lines under it (how, for whom), one button, and a picture of the product in use | A stranger knows what, for whom and what next without scrolling |
| 2 | Proof strip | Logos or names (with permission), or one counted number with its date | Nothing here is unsourced |
| 3 | The problem | The situation in a customer's own phrase | A buyer would say "that's me" |
| 4 | How it works | The real steps, numbered, each with what the customer does and gets | Could be followed as instructions |
| 5 | Outcomes | Two to four results, each with its proof beside it | Each cites an allowed claim |
| 6 | The objection | The main reason not to act, answered honestly | Uses the real objection from calls, not an easy one |
| 7 | Other readers | Short routes for the second and third buyer types ("For shops", "For teams") | Each links to its own page |
| 8 | Price pointer | The starting price with its unit, and a link to pricing | Matches the price sheet |
| 9 | Close | The promise once more in different words, and the same button | Same action as the top |

- The navigation uses the words customers use for the things they look for.
- One primary action. "Talk to us" beside "Start free" is fine only when they are the same decision at two speeds.

## Landing page

**Job:** one traffic source, one reader, one promise, one action. A landing page is the second half of a sentence the
ad, email or search result started.

1. **Match the message.** Open the ad or email that sends people here. The headline repeats its promise in nearly the
   same words. A visitor who clicked "bike repair without the shop visit" must see those words, or their meaning, at
   once.
2. **Top:** headline, the line under it, the button or short form, one picture. Ask only for what the next step needs:
   every extra field costs sign-ups.
3. **Proof** for this reader: a quote from a customer like them (with permission), or the number that matters to them.
4. **How it works**, short.
5. **The objection** this traffic source is known for (price for ad traffic, trust for cold traffic).
6. **Close** with the same action.

- Remove links that lead away from the action, except what the reader needs to trust the page (pricing, terms).
- One page per promise. Two ad groups with different promises get two pages.
- Check tracking before it ships (`check-campaign-tagging`): a page whose result cannot be read is not finished.

## Pricing page

**Job:** let a buyer find their plan, see the whole cost and decide without writing in. This skill words the page. It
never sets or changes a price, plan or term: those come from the price sheet and the owner.

| # | Section | What it holds |
|---|---|---|
| 1 | Top | One line on how pricing works ("Pay per bike, per month. Cancel any month.") |
| 2 | Plans | For each: the name, who it is for in one line, the price with unit and billing period, what is included in the buyer's terms, the button |
| 3 | Full comparison | A table of what each plan includes, with limits as numbers |
| 4 | What it really costs | Taxes, fees, set-up, overage and renewal terms, stated before the button |
| 5 | Questions | The real ones from sales and support: cancelling, refunds, changing plan, what happens at a limit, payment methods |
| 6 | Proof | One quote or number about value for money, sourced |
| 7 | Bigger needs | When to talk to a person, and what that conversation covers |

- Mark a plan "most popular" only when the counts say so, with the date.
- Show monthly and annual prices honestly: the same unit for both, the saving computed from the two real prices.
- A recurring charge states the amount, the period, that it renews, and how to cancel, next to the button. US default:
  federal law (ROSCA) and state auto-renewal laws require clear terms and consent before billing and a simple way to
  cancel. Other countries differ. Any change to how renewal or cancellation is described goes to the legal owner.
- "Free" says what is free, for how long, and what happens after.
- Every number on the page matches the price sheet of the same date. If they differ, stop and file the mismatch.

## Launch announcement

**First pick the tier.** It decides how much you make.

| Tier | When | What goes out |
|---|---|---|
| Major | A new product or plan, or a change in who would buy | The full set: announcement article, home page change, email to customers and to the list, social posts, in-app note, sales and support brief, and press through `handle-press-and-a-crisis`. Needs an OK. |
| Medium | A feature many customers asked for | An article or changelog entry, an email or in-app banner to the customers it affects, one social post |
| Minor | A fix or small improvement | Release notes only (`ship-a-release`) |

Choose by counting: how many customers it changes something for, whether it changes who would buy, and whether there
is something to show. When unsure, pick the lower tier. A loud announcement of a small thing spends trust.

**The announcement itself**, in this order:

1. What you can now do, in one sentence, from the customer's side ("You can now book a pickup for a second bike").
2. Who it is for.
3. The problem it removes, in a customer's words.
4. How it works, with a picture or a short clip of the real thing.
5. Proof: a beta customer's quote (with permission) or a counted result. If there is none yet, say what was tested.
6. Who gets it, when, and what it costs.
7. How to start: one action.
8. What comes next, only if the product owner has committed to it.

**Rules.**

- Announce only what is live for the people reading (`run-a-launch-readiness-check` when the team has it). No dates
  for future work unless the product owner confirmed them in writing.
- One story on every surface: the same promise and proof in the article, the email, the post and the sales brief.
- Support and sales get the brief before customers get the news: what changed, who has it, the three likely
  questions with answers.
- Order on the day: the product is live, then the page, then customers, then everyone else, then press.
- Seven days later, read it back: how many used the new thing, replies, support contacts, sign-ups against the
  prediction. Record it in `knowledge/results.md`.

## Article or blog post

**Job:** answer one question better than anything the reader would otherwise find. If it is meant to be found in
search or cited by assistants, also run `get-found-and-cited`'s page checklist.

1. **Title:** what the reader will get, in their words. No pun that hides the subject.
2. **Opening:** the answer or the main point in the first three sentences. No scene-setting.
3. **Body:** the reasoning, in the order the reader's questions arrive. Headings are statements a skimmer could read
   alone.
4. **The part only we have:** our data, method, test or experience. If there is none, the article should not exist.
5. **Examples:** real ones, with permission where a customer is involved.
6. **End:** the next step for the reader. No summary of what they just read.

- A byline and a date. Sources linked beside the claims they support.
- Length is set by the question. Stop when it is answered.
- **Case study:** the customer and their situation, what they tried before, what they did with us, the result in
  their numbers. Ask the customer for the numbers and get their sign-off on the final text. Never estimate a
  customer's result.

## Writing the small parts

| Part | Rule |
|---|---|
| Headline | The promise or the outcome. Read alone, it still makes sense. |
| Line under it | How it works or who it is for. Adds a fact the headline lacks. |
| Button | A verb and what the reader gets: "Book a pickup", "See plans". Says what happens next. |
| Text under a button | Removes the last worry: "Takes two minutes. No card needed." Only if true. |
| Form | Only the fields the next step needs. Labels above fields. The error says what to fix. |
| Customer quote | Exact words, name, role, company, permission on record. Pick the quote that states a result, not praise. |
| Image caption | Says what the picture shows and why it matters. People read captions. |
| Page title and description | What the page is, for the person scanning search results. |
