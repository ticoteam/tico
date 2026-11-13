---
name: build-the-brand-book
description: Build the company's brand book with the owner in a coached conversation (about an hour, then 30 minutes for the look and real pieces) - who it's for, the promise, claims, messages, voice, words, look, real pieces marked us or not us - checked by a script and handed over as a report plus a Brand card proposal.
---
# Build the brand book

When to use: your setup (the first message while Tico marks you Needs setup), a task that asks for the
brand book, or the owner asking to redo or extend it. Also to resume a run (the brand book run noted on the task).

## In Tico

- **Files.** Keep the book as `reports/brand-book.json` (format in `book-format.md`) and fill it as each stage
  finishes. Save every message the owner sends, word for word, to `reports/brand-book-transcript.json`
  (`{"turns": [{"who": "person" | "bot", "text": "..."}]}`, bot turns too) so the audit can check what they said.
  Commit both after each stage: a pause never loses work.
- **Checks.** `python3 skills/build-the-brand-book/audit.py contrast '#FFFFFF' '#F4B942'` for every ratio;
  `python3 skills/build-the-brand-book/audit.py check reports/brand-book.json reports/brand-book-transcript.json`
  for the rest. Paste its output; never a number worked out by eye.
- **Pause and resume.** When the owner stops, note the brand book run on the task: the stage, the next question,
  open threads. On their next message, pick up there in one line.
- **Teammates.** A needs-teammate item is one `tico task ask <task> --to <person> "<question>" --version N` each,
  quoting the section and what the answer unlocks. When answers arrive, follow **Resume: teammate answers** below.
- **Hand-over.** At the end:
  1. `python3 skills/build-the-brand-book/audit.py markdown reports/brand-book.json > reports/brand-book.md`, then
     `tico file publish reports/brand-book.md` (publishing again adds a version).
  2. Propose the Brand card from the book with `tico proposal create --kind brand_draft`, payload
     `{"sections": {...}}`, using only these sections and limits (the card refuses anything else):
     - `name` (≤ 60), `positioning`: the one line (≤ 120).
     - `voice`: [{trait, do, dont}] from the voice table (we are, sounds like, but not / not like).
     - `tone`: [{situation, guide}] from tone by moment (dial plus example line, ≤ 300).
     - `words`: {use: [{word, not}], avoid: [{word, why, level: warn|block}]}.
     - `naming`: [{name, rule, wrong: [forms seen]}].
     - `visual`: {colors: [{name, hex}], fonts: [family (role)], logo_files: [...], imagery: logo misuse rules,
       distinctive assets, what we show and never show (≤ 1000)}.
     - `claims`: {allowed: [{claim, proof, check: owner and review date, status: proven|due}], never: [claim]}.
     - `examples`: [{text, verdict: on|off, why}] from the real pieces.
     Story, audience, messages, personality, writing rules, ready-to-use lines, surfaces and governance stay in the
     report until the card has room for them.
  3. If the book changed the positioning or found a strategy gap, propose it with `--kind strategy_draft`.
  4. Tell the owner in three lines: where the book is, that the card change waits for their confirm and publish, and
     the needs-teammate list.
- **Routine.** At the end of a first run, switch your monthly audit on (see `playbooks/onboarding.md`).


Follow `coach.md` in this folder. This file is the method; where the two differ, this file wins (overrides at the end).

**Founder time is for what only the founder knows**: stories, taste, trade-offs. Everything you can work out from
files, rules and the founder's earlier answers is desk work: you do it between questions and show only the decisions
it raises. Aim for one sitting of about an hour (60 turns or fewer) for strategy and voice, and a second of about 30
minutes for the look and the real pieces. Say so at the start. Each stage ends with its sections filled, so a pause
never loses work.

## Primer (say this, in your own words, at the start)

- A brand is what people say about you when you're not in the room. The book makes every page, email and bot reply
  push that the same way.
- Strategy first: who it's for, what we promise, what we're up against. Then how we sound. Then how we look.
- Your customers' words and your own stories beat anything I write. The best lines will be ones you or a customer
  already said.
- I do the checking and the rule-writing between questions. You make the calls.
- At the end I write real pieces with the book and you mark each one "us" or "not us". That's the real test.

## Ground rules

- **Sources on every fact, inside the book only.** Any line that states a fact (a number, a quote, an event, a
  product property) carries its source in brackets in the book. No source, no fact: ask the founder instead. Sources
  never appear in customer-facing copy; sample pieces list their sources under the piece.
- **Taste calls are the founder's; compliance calls are yours.** Taste: values, traits, voice picks, taglines,
  sample verdicts, category words, which words to prefer. Give options with no recommendation and no "I'd take".
  Compliance: claims, contrast, names, legal risk, structure, and surface rules that rest on data (search terms,
  character limits). Recommend one option with the reason. Unsure which? Treat it as taste.
- **Never supply the founder's story.** Don't offer a past decision, a memory or a feeling as a yes/no question ("Was
  pricing at $129 a choice to sell the show?"). Ask open questions; if no story comes, there is no value.
- **Who decided, only where it matters.** Values, traits, the promise, pillars, taglines, voice lines and sample
  verdicts record `by`: `founder` (they said or chose it), `accepted` (you showed it and they kept it), or `default`
  (desk work they never saw). Names, writing rules and contrast results are rules and facts: no `by`.
- **Evidence must match what it proves.** Category reviews show what buyers want, never that we deliver it. A number
  about one buyer type (an installer's average house job) never stands in for another (buildings). A "why now" built
  only on category talk is tagged `assumption`.
- **The founder's words are theirs.** Anything marked `by: founder` or quoted as theirs is copied from their turn
  word for word (fix only capitals). A slider with no reason has no reason. The audit checks this against the
  transcript.
- **Numbers from scripts.** Contrast ratios, word counts and the cross-check come from `brand-book/audit.py`. Paste
  its output; never type a ratio or count worked out by eye.
- **Message length.** Normal turns follow the coach's 3-6 lines. Three kinds of turn may be longer, as a block with
  one question at the end: the voice test, the decisions read-back, and the sample pieces.
- **Quoted copy is exempt.** Lines quoted from current material as "not like" or "off brand" keep their faults.
  Everything we write passes the unslop check (the `unslop` skill, when the company has it in Library › Skills).

## Stage 0: Read first (desk)

Read everything before the first question, and cite each item in brackets when you use it: the published strategy
(`tico strategy show`), bets and Market page, the current Brand card (`tico brand show`), docs (`tico doc search "brand"`,
`"style guide"`, the home page); live copy (site incl. CSS,
listings, emails, support macros, sales emails, social, app copy and errors, the founder's own writing); customer
words (reviews, support threads, call notes, quote lists); product data (returns, complaints, ratings).

Build two working lists you'll use all session (they go in the book):

- **Revenue lines**: every product, plan and subscription, with price, who buys it, top complaint, return reason.
  Each one gets messages later, or is marked "not marketed" by the founder.
- **Lines that already sound like us**: the founder's own sentences and customer quotes with life in them (a story,
  a picture, a feeling). These are raw material for the voice and must be used.
- **The founder's worries about the brand**, from anything they've said or written ("is B2B the brand we set out to
  build?"). Each one gets a question in the stage it belongs to.

Then say in three to five lines what you found: inconsistencies, the top complaint, and the one or two lines that
already sound most like a real company. Ask the first Stage 1 question in the same turn.

## Stage 1: Story and values (interview only)

- **Purpose**: "Who is better off because you exist, and how?" One person, one change. **Vision**: "If this works
  for ten years, what is different?" **Mission**: "What do you do every day to get there?" Verbs, not adjectives.
- Test each against the business as it is: a line that contradicts a product or channel, or a claim you can't make
  yet (check the claims you already know are shaky), is raised now, not at the audit.
- **Values from past decisions**: "Tell me about a time you said no to money, or did something slower or dearer
  because it was right." Then "When was that, roughly?" Name the value in the founder's words, as a value ("we sell
  the show, not the box"), not a price or a tactic. Zero values is fine; a value with no story is not a value. If the
  founder insists, keep it tagged `aspiration`.

Filled: Why we exist, What we value.

## Stage 2: Audience and words

Draft from Stage 0 and the strategy without asking. Show the founder only what you're unsure of (a buyer type you
can't describe, a guess about who's primary, a quote you can't place), at most three questions in all.

- **Every buyer type** the company sells to, primary first: the best customer as one person, traits you can see from
  outside, jobs they hire us for, what they did before, where they meet us. Partners who resell (installers,
  retailers) are their own row.
- **Anti-audience**: who we don't write for, and why.
- **Customer words**: verbatim quotes with who said it and source; include negative words about the category and
  about us. Ask the founder for any they remember word for word.

Filled: Who it's for, How customers say it.

## Stage 3: Positioning and promise (interview)

Reuse the strategy and the Brand card; ask only what is missing.

- Category (what buyers type), alternatives (do nothing, the cheap option, the dear one, named rivals), and **the
  perception to beat**: what buyers fear or believe about this category or us. The brand answers it.
- **What only we have**: facts a rival can't copy soon. Check each against the Market data; if a rival has it, it
  isn't "only".
- **The promise**, tested two ways, both shown:
  1. **What breaks it**: the ways it fails today, checked against each revenue line's data. If the best-selling line
     breaks it, give options (narrow the promise, fix the product first with an owner, keep it as a goal tagged
     assumption) and recommend one; the founder picks. "Narrow" means the promise's words change now; if they don't,
     record it as "keep as a goal".
  2. **Rival swap, per buyer type**: for each buyer type, take the strongest rival *for that buyer* from the Market
     data and put their name in. If the promise still reads true for the primary buyer, it isn't a promise yet: pull
     in something from "only we have". For other buyers, their "why us" message must fail their own rival's swap.
- More than one buyer type: "one brand with a message set per buyer, or separate brands?" Give both options once,
  with no lean; record who decided. The promise must hold for every buyer type the book serves, or say which it
  doesn't cover and what they're promised instead.

Filled: Positioning and promise.

## Stage 4: Claims (desk, then decisions)

Desk work: list every claim in current copy and in what's been said so far, including implied ones ("the whole
street stops" claims a result, "we tell you which fits" claims a size guide exists) and testimonials. Allowed claims
get IDs (A1, A2...). From here on, **every product property, result or number in the promise, messages, lines and
pieces cites its allowed claim ID** in brackets in the book, e.g. "[A4]". No ID, no assertion: add the claim (with
proof) or cut the line. The audit fails any ID that isn't an allowed claim. A fact the founder or a teammate gives in
the session (a support fix, a spec) becomes an allowed claim with them as the source and owner. Customer praise that
compares us ("brighter than the $40 one") may be quoted as a testimonial with permission; it never becomes our own
comparative claim, and the word it uses isn't avoided. For each: proof, source, owner, review date, or never. Superlatives
need a source or go to never. Never-claims each get a reason and a **Catch** list: the phrases that would give the
claim away in copy (for "Home Starter needs no ladder": "no ladder; nobody climbs a ladder; off the ladder"). The
audit scans the whole book for them, but rewording around a Catch phrase is still the claim ("what fits" is "which
projector fits"): add the new wording to Catch.

Show the founder only the rows that need a decision (a claim we can't prove that they like, a number to confirm, a
permission to get), with your recommendation. Check Stages 1-3 against the never list now and fix them.

Filled: Claims.

## Stage 5: Messages

Draft every buyer's messages and show them in one block, one short table per buyer; ask which lines are wrong. Each buyer gets three labelled
pillars: **why this kind** (against doing nothing or the old way), **why us** (from "only we have", and it fails that
buyer's rival swap), **why now** (a real date, change or cost; if there is none, say so). Each pillar: the message,
what it means for that buyer, proof that proves that exact message (an allowed claim), where it leads.

Every revenue line from Stage 0 appears in at least one message or is marked "not marketed" by the founder. Pitch
size matches the buyer: a $129 buyer isn't sold with building counts.

Filled: What we always say.

## Stage 6: Personality and traits (interview)

Ask all five sliders in one message, 1 to 10, each with both poles and a "why" (formal-casual, serious-playful,
reserved-bold, plain-technical, warm-matter of fact). Then follow up only on unclear ones ("8, more serious" means
which end?) and on 5s ("if you had to lean?"). Point out where current copy disagrees with their scores.

Traits: ask "If a customer described how we talk, what three words would you want them to use, and what's the bad
version of each?" Use their words. If they're stuck, offer more than three pairs built from the sliders and the
perception to beat, with no recommendation; they pick and edit. Traits are never set by default.

Filled: Personality.

## Stage 7: Voice test

1. Take two real messages the company sends: one happy (a launch, a welcome) and one hard (the top complaint).
2. Write each in three voices, A, B, C, under 60 words, that differ in **approach**, not wording: A plain and short;
   B built on the founder's own story or a customer's words from "lines that already sound like us"; C pushing the
   boldest slider further. For the hard message, the three must differ in what they do (own it and fix it; explain
   why it happens; turn it into the next good thing), not just in tone.
3. Ask: "Which is us? You can mix." No recommendation.
4. Write the voice table from the pick: we are, but not, sounds like, not like (real current copy or rejected lines),
   and the **rhythm**: sentence length and patterns the pick shows (short lines, a picture then a fact, questions or
   none). Each trait needs one "sounds like" line from the company's own story or its customers' words. Any line the
   founder calls "most us" goes in as they said it; never drop or reword one.
5. Rival swap on the **trait set**, not single lines: would the strongest rival's copywriter accept this whole voice
   table as theirs? If yes, ask the founder which trait to push further or what only they would say. A short common
   line ("That's on us") can be very us; it's the set that must be distinctive.

Two rounds without a pick: record the founder's words as the trait, tag `default`, move on.

Filled: Voice.

## Stage 8: Desk work, then one read-back

Without asking, draft from everything decided so far:

- **Tone by moment**: first hello, selling, good news, bad news or apology, an error, support, social, partner or
  investor, one per extra buyer type. Dial and example line. Clarity beats voice.
- **Words**: use (customers' words first) and avoid (reason, warn or block), starting from current clichés, the
  unslop list and the negative customer words. Don't avoid a word customers use to praise us.
- **Names**: every name in current material, right form, wrong forms actually seen (cite where), legal name.
- **Writing rules**: case, numbers, dates, money, emoji, exclamation marks, punctuation, reading level, dated facts,
  inclusive and accessible writing (holidays named, alt text, link text, plain language). Each checkable by a person
  or a regex; say which. Only rules the material or the founder gave a reason for.
- **Surfaces**: each place the company speaks, with constraints, pattern, example and never. Listings keep the search
  terms buyers type but no never-claims. Errors say what happened and what to do. **Support replies give the real fix
  for each of the top two complaints**, ending with a step the customer can take today: ask the founder for the fix if it isn't in the files; a placeholder is an open item with
  an owner.

Run `audit.py check book.json` on the draft and fix what it finds. Then send **one decisions read-back**: at most four
numbered choices, the ones that would change copy most (a category word, a word customers love that you'd warn on, a
surface rule that costs sales, a support fix you don't know), each with options. Everything else is listed as
"decided at the desk" in one line per section, and stays `default`. Desk work never adds lines to taste sections
(values, traits, voice, taglines): those hold only what the founder said or picked.

Filled: Tone by moment, Words, Names, Writing rules, Where we speak.

## Stage 9: Ready-to-use lines

Tagline: three or four options that differ in approach (a promise, a picture, a line from the founder's story or a
customer); each must fail the rival swap. No recommendation; the founder picks or writes their own. Then draft a
25-word description, a 100-word About (leads with what only we have, covers every marketed revenue line), sign-offs
for good news and complaints, and the main call to action. Counts from the audit script.

Filled: Ready-to-use lines.

## Stage 10: Look (desk, then decisions)

Start from facts; never invent a hex, font, file or logo rule. First ask three taste questions, one at a time,
with options and no lean: which colour leads and roughly how much of each; how the type should feel (and whether the
display font fits the "but not"s); what the photos show. Then draft colours with roles, type with roles, logo files,
contrast only for pairs in use and marks that carry meaning (from the script), imagery and motion. Show only the
decisions: failing pairs in use (offer the nearest passing shade), at least three logo misuse rules,
distinctive assets and how to protect them, what we never show. If the facts are thin, offer a one-page designer
brief with an owner instead of guessing.

Filled: Look.

## Stage 11: Real pieces

Write at least six pieces with the book, each under 80 words: website hero, a listing title and first bullet, a
seasonal email subject and opening, a support reply to the top complaint (with the real fix), a social post, one per
extra buyer type, and one for a non-hero revenue line (a subscription, an add-on). Use the "most us" lines and at
least two customer lines; no single story in more than two pieces. Sources listed under each piece, never in it. Ask
for a reason on at least two verdicts ("what makes this one us?") and fold the answer into the voice.

The founder marks each "us" or "not us". Show two off-brand versions (one real current piece, one that breaks a "but
not") and name the rule each breaks. For each "not us", ask what's wrong, fix **the rule** that allowed it, then the
piece. Up to two rounds. A piece changed after its verdict (by a fix or the audit) is shown again before it counts as
"us".

Filled: On and off brand.

## Stage 12: Audit (desk)

1. Run `audit.py check book.json transcript.json` on the whole book (the transcript is the conversation so
   far). Fix every finding or record why it stands (a "no ladder" inside the
   Facade row is fine). Report the before and after counts.
2. Read each section against its bar in `template.yaml` and quote the line that meets each part; no quote, no
   pass. Count, don't estimate (five quotes means five rows). A bar that only a teammate can close (a quote permission, a
   product fix, a designer's sizes) is **needs teammate**: one open item with the owner and the exact question. Any
   other failure is fixed now.

**Taste bars vs fact bars.** A distinctiveness bar (vision a rival could paste, a "sounds like" line that survives
the swap, a tagline) is a taste bar: show the swap result and one stronger option once; if the founder keeps theirs,
record it as "founder's call, turn N" (the turn where they kept it) with the swap result, and the bar counts as
closed. No such turn, no founder's call: ask now. A fact bar (proof, permission,
contrast, a never-claim, a number) never closes on the founder's call.

## Stage 13: Finish

1. **Rule pack**: at most 12 rules a bot loads before writing: the promise and what we never promise, the traits
   with their "but not", the moments where the voice changes, the top writing rules, the sign-offs, the line on
   sources ("sources stay in the book, never in copy"). No word or name lists: Tico checks blocked words, names and
   never-claims itself. It replaces the Brand card summary for growth-facing bots.
2. **Governance**: owner by name, approvers, review date (default every six months and before any launch or
   rename), what Tico checks automatically, the needs-teammate list.
3. **Ask the teammates.** Each needs-teammate item goes to its owner as one question (in Tico: an ask on the task, or
   a message to their bot), quoting the section and what the answer unlocks. Facts a teammate holds come from them,
   not second-hand through the founder: tag second-hand ones `assumption` until the owner confirms.
4. Headline, decisions, assumptions, next steps, as in `coach.md`.
5. Hand it over as in **In Tico** above: the book published as a report, the Brand card sections proposed, a
   positioning change to the strategy proposed if the book changed it. Never publish; the owner publishes.

## Overrides of `coach.md`

- Rule 5 (short messages): three kinds of turn may be longer (see ground rules).
- Rule 11 (their call): a value or claim that fails its bar keeps the founder's wording but is tagged (`aspiration`,
  `assumption`) and listed as open. Their call never turns a failed bar into a pass.
- Rule 14 (stop at the bar): per section. The book is done when every bar passes, is a recorded founder's call (taste
  bars only), or is needs-teammate with an owner.

## Resume: teammate answers

When answers come back, resume the run: put each answer in its section with the teammate as source, run
`audit.py check book.json`, re-read only the bars those answers touch, and show the founder only what changed a decision (a
claim that can now be made, a fix that changes a support reply). Then a new draft for the owner to publish.

## Common failure modes

- Asking the founder to approve desk work section by section ("Keep" fifteen times).
- Suggesting the founder's story for them; recommending on taste calls, so the founder just says "One."
- A safe voice: traits any company could have, the founder's best lines dropped or reworded, one story carrying
  every piece while better customer lines sit unused.
- An assertion with no claim ID ("fits two-storey houses") because it sounded like a fact the founder knew.
- Grading your own bars generously: quote the evidence or it fails.
- A promise, purpose or hero that uses a phrase from the never list. Run the script; don't trust a read-through.
- Evidence about one buyer used for another; category talk used as our proof or our "why now".
- A revenue line with no message; support replies without the fix.
- Inventing proof, hex codes, quotes, numbers or "wrong forms seen"; contrast numbers typed by hand.
- A book that describes the brand but never shows real copy written with it.
