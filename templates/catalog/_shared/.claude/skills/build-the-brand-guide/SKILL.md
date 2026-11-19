---
name: build-the-brand-guide
description: Build, update or apply the company's brand guide - fixed voice traits each with a "but not", a tone matrix by moment, words to use and avoid, names, allowed and never claims, visual tokens (colour, type, logo, imagery) and real on-brand and off-brand examples. Derives the voice from the company's best existing copy and its customers' own words first, then asks the owner only the taste calls. Also reviews any piece of copy or any asset against the guide, line by line, with the smallest fix. Use when a task says brand guide, brand book, style guide, voice and tone, tone of voice, brand voice, visual identity, brand colours, logo rules, "is this on brand?", brand check, brand review, or when a bot has to write for the company and no Brand card is published yet.
metadata:
  function: marketing
  roles: [design-owner, content-owner, ux-writer, segment-marketing-owner, pr-manager]
  actions: [read, draft, change-records]
  review: none
---
# Build the brand guide

When to use: a task asks for the brand guide (or brand book, style guide, voice and tone), asks to change or extend it,
or asks whether a piece is on brand. Also your setup, if you are the bot that owns the brand.

Words used here: **the company** is the business whose brand this is. **The team** is everyone in Tico, people and
bots. **The owner** is the person who owns the brand's taste calls, by default the team's owner. **The Brand card** is
Tico's structured copy of the guide (Strategy > Brand) that growth-facing bots get in their run context.

Three modes, all in this skill:

- **Build**: no guide yet, or a rebuild. Steps 1 to 11.
- **Update**: one section changes (a new product name, a new claim, a new colour). Do the matching step, then steps 9
  to 11.
- **Review**: check a piece or an asset against the guide. Step 12.

## What great looks like

A contractor who has never met the company reads the guide for ten minutes and writes a page the owner would sign. That
is the test, and it is why a great guide is mostly real lines, not adjectives.

- **Voice is fixed, tone moves.** Three or four traits that never change, each with a "but not" that names its bad
  version. A tone matrix says how those traits flex with the reader's situation: a first hello, a sale, an outage, an
  apology. Clarity beats voice every time, and when unsure the writer stays plain. Humour is optional, never required.
- **The voice is found, not invented.** It comes from the best things the company already wrote and the words its
  customers already use. Every trait is shown by lines that exist.
- **A rival could not use it.** Put the strongest competitor's name on the promise and the voice table. If they still
  read true, the work is not done.
- **Rules a bot can test.** Words, names, claims and contrast ratios are checkable. Visuals are tokens (a name, a role,
  a hex code, a font with its fallback), not descriptions.
- **Short where it is used.** The full guide is a page. What a bot loads before writing is at most 12 rules.
- **It is kept.** One named owner, a version, a review date, and a log of what changed and why.

## First time at a company

Read `knowledge/brand.md` and run `tico brand show`. If the setup answers are there, skip this. If not, send the owner
these in one numbered message, save the answers with the date under "Setup answers" in `knowledge/brand.md`, and start
work on the defaults at once. Do not wait for the reply.

1. Which three to five pieces sound most like us: a page, an email, a post, a reply you wrote yourself? Default: I
   pick them from evidence (step 2) and show you which and why.
2. Who do we write for first? Default: the primary buyer in the Strategy section "Who we serve".
3. What must we never say, promise or claim? Default: every claim I cannot source, every superlative, and anything
   about a competitor by name.
4. Who owns the guide and who may change it? Default: you publish the Brand card; the bot that runs this skill keeps
   the guide current; it is reviewed every six months and before any launch or rename.
5. Where are the logo files, colours and fonts? Default: I read them from the live site's CSS and from Library > Files,
   and I invent none.
6. How much humour, and where is it off limits? Default: plain and warm; no jokes in errors, billing, apologies,
   security or legal messages.

## Steps

### Build

1. **Read everything before you ask anything.** Cite each item in brackets when you use it.
   - Strategy and brand: `tico strategy show`, `tico brand show`, `tico doc search "brand"`,
     `tico doc search "style guide"`, the Market tab (`tico market show`) for rivals.
   - Live copy: the site and its CSS, listings, emails, support macros, sales emails, social posts, app copy and error
     messages, and anything the founder wrote by hand.
   - Customer words: reviews, support threads, call notes, quote lists.
   - Product facts: plans and prices, returns, complaints, ratings.
   Keep three working lists: **revenue lines** (each product and plan with price, buyer and top complaint), **lines
   that already sound like us** (sentences with life in them, the owner's and customers'), and **claims in current
   copy** (stated and implied).
2. **Derive the voice from the best existing copy.** Follow `references/derive-the-voice.md`. In short: build a
   "best" set of 10 to 20 pieces chosen on evidence and a "not us" set of at least five; pull the sentences that carry
   each best piece; find what most of those sentences share that the "not us" set lacks; name three or four traits in
   the company's own words, each with a "but not" and at least two real lines as proof.
3. **Ask the owner the taste calls, once.** One message with the voice test: two real messages the company sends (one
   happy, one hard) each written three ways that differ in approach, not wording. Ask "Which is us? You can mix." Give
   no recommendation. Taste calls are the owner's: traits, voice picks, taglines, which words to prefer, verdicts on
   sample pieces. Compliance calls are yours: claims, contrast, names, legal risk, structure. On a compliance call,
   recommend one option with the reason. If no answer comes, keep the derived traits, tag them `default`, and go on.
4. **Settle the promise.** One line: who it is for, what they get, against what belief or alternative. Test it two
   ways and record both results: what breaks it today (checked against each revenue line's complaints), and the rival
   swap (the strongest rival's name in place of ours). A promise that survives the swap is rewritten with something
   only the company has.
5. **Sort the claims.** List every claim in current copy, including implied ones and testimonials. Each is **allowed**
   (an ID such as A1, the proof, its source, an owner, a review date) or **never** (the reason, and its Catch phrases:
   the wordings that would give the claim away in copy). From here on, every product property, result or number in
   the guide's own lines cites an allowed-claim ID in brackets. Superlatives need a source or go to never. A customer's
   praise that compares us may be quoted as theirs with permission; it never becomes our own comparative claim.
6. **Write the tone matrix.** One row per moment: who is reading and how they feel, which trait leads, what to dial
   up, what to dial down, and one example line. At least eight moments, including bad news, an error message and an
   apology. Start from `references/guide-template.md`.
7. **Write the words, names and writing rules.** Words to use (customers' words first) and to avoid (each with a
   reason and a level, warn or block). Never avoid a word customers use to praise the company. Every name in current
   material with its right form and the wrong forms actually seen, each with where it was seen. Writing rules only
   where the material or the owner gave a reason, each one checkable by a person or a pattern.
8. **Set the visual tokens.** Colours with a name, a role and a hex code. Type with family, role, sizes and a fallback
   font. Logo files, versions, smallest size, clear space and at least three misuse rules. Imagery: what we show and
   never show. Every hex, font and file comes from a real file or the owner. Compute the contrast ratio for every
   colour pair in use with the formula in `references/review-copy.md`, with a tool call, never by eye. If the facts are
   thin, write a one-page designer brief and file it as a sub-task instead of guessing.
9. **Prove it with real pieces.** Write at least six pieces with the guide, each under 80 words: the website hero, an
   email subject and opening, a support reply to the top complaint with the real fix, a social post, an error message,
   and one for a product that is not the hero. Add two off-brand versions and name the rule each breaks. Ask the owner
   to mark each "us" or "not us". For each "not us", fix the rule that allowed it, then the piece.
10. **Write the rule pack and the governance.** The rule pack is at most 12 rules a bot loads before writing: the
    promise and what we never promise, the traits with their "but not", where the tone changes, the top writing rules.
    No word lists in it: `tico brand check` already checks avoided words, names and never-claims. Governance: the owner
    by name, who may change what, the review date, the version, and one line per change.
11. **Publish and hand over.**
    - Write the full guide to `knowledge/brand.md`, rule pack first, setup answers and the change log last.
    - Build the guide page as HTML (layout in Output) and publish it:
      `tico page publish reports/brand-guide.html --title "Brand guide" --cadence none`.
    - Propose the Brand card: `tico proposal create --kind brand_draft` with the sections and limits in
      `references/guide-template.md`. The owner publishes it.
    - If the work changed the positioning, propose that too: `tico proposal create --kind strategy_draft`.
    - File one sub-task per missing fact to the teammate who holds it (`tico task file "<question>" --parent <id>
      --owner <slug> --message "<section, the exact question, what the answer unlocks>"`).
    - Tell the owner in three lines: where the guide is, that the card waits for their publish, and what is still open.

### Review

12. **Review a piece or an asset against the guide.** Follow `references/review-copy.md`. In short: read the Brand
    card and `knowledge/brand.md` first; read the piece once as its audience and write what the reader takes away;
    then go line by line. A line that breaks a rule gets the rule quoted and the smallest fix. A line that only
    breaks taste is marked "suggestion". Check every fact, name, price and date against a source. Run the compliance
    pass even when no guide exists. Put the verdict on line one: on brand, on brand with small fixes, or off brand.

The monthly audit of what went public is its own sub-step: use the `score-a-brand-audit-sample` skill when the team
has it, and feed its three fixes back into the guide's rules.

## Gates

- **No source, no fact.** Every number, quote, event and product property in the guide carries its source in brackets.
  Sources never appear in customer-facing copy.
- **Nothing invented.** No hex code, font, logo rule, customer quote, "wrong form seen" or story that you did not find
  in a file or hear from a person. A gap is written as a gap with an owner.
- **Never supply the owner's story.** Ask open questions. A value with no story from the owner is not a value.
- **A trait needs proof.** Each trait has a "but not" and at least two real lines, one from the company's own story or
  a customer. A trait with no lines is cut.
- **The swap must fail.** The promise and the trait set do not read true with the strongest rival's name on them, or
  the owner kept them anyway and the guide records that call with the swap result.
- **Claims hold.** No line in the guide asserts a product property, result or number without an allowed-claim ID. No
  Catch phrase of a never-claim appears anywhere in the guide. Run `tico brand check -` on every outward line you
  wrote.
- **Contrast is computed.** Normal text needs at least 4.5:1 against its background and large text 3:1 (WCAG 2.2, AA).
  Ratios are not rounded: 4.49:1 fails. A failing pair in use is shown with the nearest passing shade.
- **Quotes need permission** before they appear in public examples. Inside the guide they need only their source.
- **A review marks every change** as rule or suggestion, quotes the rule, and never rewrites a paragraph where a line
  would do.

## Autonomy

Do on your own, without asking:

- Read everything, derive the voice, and draft the whole guide on the defaults.
- Write `knowledge/brand.md` and publish the guide page inside the team, as many versions as it takes.
- Review any piece or asset and give the verdict. A brand review needs no person.
- File sub-tasks for missing facts, and fix your own drafts.
- Propose the Brand card and a strategy change.

Needs a person:

- **Publishing the Brand card.** Tico lets only the owner publish it. You propose; they click.
- **Taste rows.** Traits, taglines and sample verdicts stay tagged `default` until the owner picks. Work goes on with
  the defaults; nothing waits.
- **A change to the name, the logo, the positioning line or a never-claim.** It goes as a proposal with the reason.
- **A name or mark that may clash with someone else's.** Run `run-a-name-clash-check` if the team has it, and send
  the result to the legal owner before the name is used.

## Output

- **The guide page** (HTML, one page). Top screen: the promise, the traits with their "but not", and the review date.
  Then the tone matrix as a table; colours as real swatches with name, role, hex and the computed ratio on each pair;
  type as live specimens; logo do and don't side by side; on-brand and off-brand pieces in two columns with the rule
  each off-brand piece breaks. Show, don't describe.
- **`knowledge/brand.md`**: rule pack, the full guide in text, setup answers, governance and change log.
- **A Brand card proposal** waiting in the owner's Needs you.
- **Sub-tasks** for open facts, each with an owner and the exact question.
- **For a review:** a comment on the task, verdict first, then the line-by-line changes.

## Checks before you finish

- Every trait has a "but not", two real lines with sources, and a `by` tag: owner, accepted or default.
- The tone matrix has at least eight moments, including bad news, an error and an apology.
- Every allowed claim has proof, a source, an owner and a review date. Every never-claim has Catch phrases.
- `tico brand check -` ran on the guide's own lines, and its output is on the task.
- Every colour pair in use has a computed ratio and a pass or fail. No hex or font is a guess.
- At least six real pieces and two off-brand ones are in the guide, with the owner's verdicts or `default`.
- The rule pack is 12 rules or fewer and none contradicts the guide.
- The guide names its owner, version and next review date.
- Anything you could not read is listed as not checked, never as fine.

## Example

Invented, for illustration only.

"Brand guide v1 for Larkfield Bikes (commuter bike repair by subscription). Best set: 14 pieces, chosen on evidence: the
3 emails with the most replies on record, 4 founder-written posts, 5 support replies rated 5 of 5, the pricing page, the
launch note. Not-us set: 6 (two agency ads, a templated newsletter, three rival pages).

Traits, derived then picked by the owner in the voice test:
1. Shop-floor plain, but not blunt. 'Your chain is stretched. We swap it Thursday.' [support reply, 2026-09-14]
2. On the rider's side, but not chummy. 'If we can't fix it by your commute, we lend you a bike.' [pricing page; A3]
3. Dry, but never at the customer's cost. 'It rained. Your brakes noticed.' [founder post, 2026-03-02]

Swap: with the strongest rival's name on it, trait 2's line fails, because they have no loan bikes [Market: players].
Claims: A3 'a loan bike if we miss your commute' (proof: service terms v4, owner: ops, review 2027-05). Never: 'fastest
repair in the city' (no source; Catch: fastest, quickest in town, nobody is faster).
Colours: Ink #1B1F23 on Paper #FAFAF7 = 15.85:1, pass. Signal #F4B942 on Paper = 1.69:1, fail for text. Ink on Signal =
9.36:1, pass, so Signal is a background for Ink text and never a text colour on Paper.
Open: logo clear-space numbers (sub-task to the designer). Brand card proposed; waiting for the owner's publish."
