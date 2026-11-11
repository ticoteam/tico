# App store method

The standing method behind `playbooks/weekly-app-store-review.md` and `playbooks/change-a-listing.md`. `listings.md` holds
the listings as they are live, `reviews.md` the reviews and their reply status, and `store-rules.md` the rules as read.
This file holds how to read a store's numbers, how listing tests work and what the rules ask of a reply, so each run does
not re-derive them. Every store rule quoted here must be re-read from the store's own page before it is used.

## Read the numbers the store shows

- Impression to install is the store's own conversion for a listing: of the people who saw the listing, how many installed.
  Read it per store and per listing. Never add the two stores together.
- Impressions, product page views and installs are different counts. A search impression is not a product page view. Name
  the count you use.
- The store shows a rating for the current version and one for all versions. A new version can move the current rating a
  lot while the all-versions rating barely moves. Name which one the trend uses.
- A rating built on few ratings moves a lot. Say how many ratings sit under a number before you call a trend.
- Exports lag. Record the date each export was pulled and the window it covers.

## Listing tests belong to the store

- Both stores offer a way to test listing elements on the store page itself. Google Play has store listing experiments in
  the Play Console. Apple has product page optimization in App Store Connect. Check each console's current name, limits and
  split rules before you propose a test. This file does not record them.
- A store test runs in the store's own tool with the store's own split. Read it with the same discipline as a website
  test: a stop rule written before the test, no early calls, and a "no clear difference" result logged as a result.
- A listing change that is not tested in the store is still logged, with the numbers before and after, and read after
  28 days.

## What a reply and a review may say

- Each store has rules on what a developer may say in a reply and on incentives. Store rules are in
  `knowledge/store-rules.md`, to be checked before quoting. Read each store's current guideline page and record the
  clause and its date in `knowledge/store-rules.md` before a reply is drafted.
- A reply is public and stays up. It answers the point, names one route (a support address or the in-app help), and does
  not argue with the reviewer or name a person.
- A bug report in a review goes to the product owner as a task. A reply to it says the team has heard the report. It does
  not promise a fix, a version or a date unless the product owner approved those words.

## Keywords and listing copy

- A keyword is worth targeting when people search it and the listing can appear for it. Check each keyword against the
  store's own search results, dated, before it goes into a title, subtitle, keyword field or description.
- Each store's limits on title length, subtitle, keyword field and description differ and change. Read them from the
  store's current page before copy is drafted, and record the limit with its date.
- A number, an award, a ranking or the words "number one" in copy keeps its dated source. An unsourced claim is removed, or
  marked for the owner. Store rules can also restrict these claims, so read them too.

## Sources

- 2026-11-07: method written at card creation. The store rules, the console feature names and their limits are not recorded
  here as fact. Each one is read from the store's own page, dated in `store-rules.md`, before it is quoted.
