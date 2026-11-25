---
name: write-marketing-copy
description: Write, rewrite and ship the company's marketing copy - homepage, landing pages, pricing page, launch and feature announcements, and blog or content articles - then read the result. Diagnoses first (blank page, a draft with a real voice, or a point edit; and whether copy is the problem at all), gathers real customer words and proof, sets one message spine per buyer, drafts from a proven structure, and passes every line through three tests (picturable, provable, not swappable onto a rival's page), a claims gate and an AI-tell gate before it goes live. Use when a task says write or rewrite copy, homepage, landing page, pricing page, headline, tagline, value proposition, call to action, launch announcement, product announcement, feature announcement, blog post, article, case study, "make this convert", "this sounds like AI", "punch this up" or "messaging".
metadata:
  function: marketing
  roles: [content-owner, segment-marketing-owner, cro-owner, ux-writer, release-notes-writer, growth-manager]
  actions: [read, draft, send]
  review: none
---
# Write marketing copy

When to use: a page, announcement or article needs writing or rewriting, or a page gets visits and does not convert.

Words used here: **the spine** is the message behind every piece for one buyer: who they are, their problem in their
words, the promise, the proof and the one action. **A claim** is any statement a reader could check. **A gap marker**
is `[NEED: what is missing, who has it]`, written where a fact should be and is not known yet.

Tico words: **the owner** is the person who runs the team and gives OKs. `knowledge/<file>.md` is a file in your own
knowledge folder, kept between runs. **A task** is one piece of work with its own thread. **An ask** is a question put
to a person on a task, and **an OK** is their yes to the exact version shown (`tico task ask <task-id> --to <person>
--kind ok "<question>" --show page:<n> --version N`). **A page** is an HTML report published into Tico with `tico page publish
<file.html> --title "<title>"`. **A Tool** is a connection to an outside system (the site, mail, ads, analytics) that
this bot has been granted. **Growth Experiments** is the pipeline for a change that carries a prediction: its
**Go-live page** shows the change and the predicted number, and a person OKs it before it runs. **The Brand card** is
the company's published voice, names and allowed claims (`tico brand show`).

For emails in a sequence and ad variants at scale, use `run-lifecycle-and-paid`, which calls this skill for the words.
For a press release or a pitch, use `handle-press-and-a-crisis`. For release notes, use
`ship-a-release` (its step 5).

## What great looks like

- **Five seconds.** A stranger who reads the headline, the line under it and the button knows what this is, who it is
  for and what happens when they click.
- **Every line passes three tests.** The reader can picture it. The company can prove it. A rival could not paste it
  onto their own page.
- **It is in the customer's words.** The phrases come from calls, reviews and support threads, not from the team's
  internal vocabulary.
- **One page, one reader, one action.** Everything on the page moves that reader toward that action or is cut.
- **It sounds like this company**, as the Brand card defines it, and like a person wrote it.
- **It ships and gets read back.** Copy that is not live has done nothing. Each piece carries a prediction, and the
  result is recorded.

## First time at a company

Read `knowledge/messaging.md`. If the setup answers are there, skip this. If not, ask the owner these in one numbered
message, save the answers there with the date, and start on the defaults at once.

1. Who is the one buyer we most want, and what do they use today instead of us? Default: the first segment in the
   Strategy section "Who we serve", and the alternatives on the Market tab (`tico market show`).
2. What is the one action we want from them? Default: the main button on the current home page.
3. What proof may I use: which customers agreed to be named, which numbers are public? Default: only the allowed
   claims on the Brand card. No customer is named until the permission is on record.
4. May I name competitors? Default: no. I describe the alternative ("booking a shop visit") without naming a company.
5. What may I publish without an OK? Default: edits to existing pages, new articles, social posts and notes on small
   updates, when every claim is already allowed and the piece has none of: a price or plan change, a new promise or
   guarantee, a named customer, a named competitor, or a statement about security, compliance or the law. Rewrites
   of the home page and pricing page, and major launches, go through Growth Experiments with a Go-live page.
6. How does copy go live? Default: through the site or mail Tool I have been granted. With no Tool, I publish the
   final copy as a page and file a task for the teammate who owns the site.

If there is no Brand card (`tico brand show` is empty), write in plain, direct language, say so in the output, and
suggest `build-the-brand-guide`. Do not wait for it.

## Steps

1. **Diagnose before you write.** Two questions.
   - *What did you get?* (a) A blank page or a generic draft: build from the spine. (b) A draft with a real voice in
     it: keep the voice, fix the structure and the proof, and change as little as you can. (c) A point edit ("fix
     the headline"): change only that, and list anything else you noticed without touching it.
   - *Is copy the problem?* Pull the page's numbers: visits, where they come from, the conversion rate, the window
     and the date (use `find-the-leak-in-a-funnel` when the team has it). Read the ad, email or search result that
     sends people there. If visitors are the wrong people, the offer is weak, or the page is broken on a phone, say
     that first. New words will not fix it.
2. **Gather the raw material.** Do not draft from memory.
   - `tico brand show`, `tico strategy show`, `knowledge/brand.md`, `knowledge/messaging.md`.
   - Customer words: `tico doc search` for call notes, support threads, reviews, survey answers and lost-deal notes.
     Copy at least ten phrases word for word with their source: how they describe the problem, what they tried, what
     made them choose, what nearly stopped them.
   - Facts: the current price sheet, the product as it is in the current release, the allowed claims
     (`tico brand show --section claims`), `knowledge/results.md` for what past copy did.
   - Where a fact is missing, write a gap marker and file one sub-task for the teammate who has it
     (`tico task file "<question>" --parent <id> --owner <slug>`). Never fill a gap with a likely value.
3. **Write the spine.** In `knowledge/messaging.md`, one block per buyer segment:
   - Reader: one person, described by what you could see from outside. The moment that makes them look.
   - Problem: in a phrase a customer actually used.
   - Promise: one sentence saying what changes for them.
   - Proof: three items, each tied to an allowed claim ID or a named source.
   - Objection: the main reason they would not act, and the honest answer.
   - Action: the one thing to do next, and what happens right after.
   Then swap in the strongest rival's name. If the spine still reads as true, it is not specific enough: go back to
   the proof. A spine already on file is reused, not rewritten, unless the owner or the results say it failed.
4. **Pick the structure** for the piece from `references/page-structures.md`: home page, landing page, pricing page,
   launch announcement or article. Decide the launch tier there before you write an announcement.
5. **Draft.**
   - Headline first: at least five candidates from different angles (the outcome, the problem, a specific proof, who
     it is for, the alternative it replaces). Choose with the three line tests. Keep the two runners-up for a test.
   - Then section by section. Each section does one job and ends when the job is done.
   - The button says what happens next ("See plans and prices", "Book a pickup"), not "Submit" or "Get started".
   - Write the first draft fast and long, then cut. Most drafts lose a fifth and improve.
6. **Run every gate** below on every line. Fix by rewriting the thought, not by swapping one word.
7. **Read it cold.** Read only the headline, the line under it and the button: does a stranger know what, for whom
   and what next? Read the whole piece aloud once: anything you would not say to a customer's face comes out. Read
   it at phone width. Cut every sentence the reader would not miss.
8. **Cut the versions.** The same spine becomes the email, the social posts, the ad lines and the in-app note (use
   `cut-a-post-into-channel-versions` when the team has it). Each version is rewritten for its channel, never
   shortened by trimming the end.
9. **Ship.** Inside your limit: publish through the Tool, check the live page on a phone, and log the piece, the date
   and the prediction in `knowledge/results.md`. Outside it: file the piece in Growth Experiments with a Go-live page
   showing the copy laid out, the old version beside it, the claims table, and a prediction as a number ("sign-up
   rate on /commute from 2.1% to 2.8% over 28 days, about 3,000 visits"). One ask, with the page shown.
10. **Read the result.** At the end of the window, compare with the prediction and record it in
    `knowledge/results.md`, including when nothing moved. For a real comparison of two versions, use the test rules in
    `run-lifecycle-and-paid`. Lines that won go into the spine in `knowledge/messaging.md`. So do the ones that lost,
    marked as lost.

## Gates

- **Picturable.** The reader can see it happen. "We collect your bike from your office by 10 and return it by 5"
  passes. "Seamless service" does not.
- **Provable.** Each claim is on the allowed list or has a dated source you can name. Numbers carry their unit,
  window and date. "Fast", "easy", "best" and "leading" need the measurement behind them or come out.
- **Not swappable.** Put a rival's name on the line. If it still works for them, it is not doing any work for you.
- **Claims gate.** Run `check-a-claim-before-it-ships` when the team has it. Without it, by hand:
  - No customer quote is written, reworded or merged. Named customers and logos have a recorded permission.
  - A result shown is one a typical customer gets, or it says plainly that it is not.
  - A comparison with a competitor has a dated, like-for-like source, and needs an OK.
  - "Free", a guarantee and a price each appear with their terms in the same view.
  - A paid or insider relationship is stated next to the endorsement.
  - Statements about security, compliance, health, money or the law go to the legal owner as a sub-task.
  - No gap marker is left in anything that ships.
- **AI-tell gate.** Check against `references/ai-tells.md`. Zero hits in the headline, the line under it and the
  button. In the body, each hit is rewritten from the thought behind it.
- **Brand gate.** `tico brand check -` on the final text passes. The tone matches the moment's row in the tone matrix.
- **No false pressure.** No countdown, "only 3 left" or "price goes up Friday" unless it is real and has a source.
- **One action.** A page asks for one thing. A second button is a quieter path to the same decision, not a rival.
- **Prices come from the price sheet** of the same date. This skill never changes a price, a plan or its terms.
- **A detector score is not a gate.** If someone raises one, use `handle-a-detector-score`. The gates above are the
  test.

## Autonomy

Do on your own, without asking:

- Draft anything. Publish drafts and comparisons as pages inside the team.
- Publish, inside the limit set in question 5: edits to live pages, articles, social posts, notes on small updates.
- Fix a factual error, a dead link, a stale price or a broken name form on a live page the moment you find it.
- File sub-tasks for missing facts, and keep writing around the gaps.

Needs an OK (one ask, with the page shown, through the Go-live page where the pipeline has one):

- A rewrite of the home page or pricing page. A major launch announcement.
- Any piece with a named customer, a named competitor, a new promise or guarantee, or a change to how a price or
  plan is described.
- Anything the legal owner flagged.
- Spend: a paid placement, a sponsored post, a stock or media licence above the budget.

## Output

- **The copy as a page** (HTML, `tico page publish ... --cadence none --task <id>`): the piece laid out in reading
  order as the reader will meet it, at phone and desktop width, with real text in every slot. For a rewrite, the old
  version beside the new one.
- **Under it**: the headline runners-up; a claims table (line, claim ID or source, date); the open gap markers with
  who was asked; the prediction.
- **Records**: the spine in `knowledge/messaging.md`; the piece, date, prediction and result in
  `knowledge/results.md`.
- **Live**: the published URL, checked on a phone, or the task handing it to the site's owner.

## Checks before you finish

- The headline, the line under it and the button alone tell a stranger what, for whom and what next.
- Every claim has an ID or a source in the claims table. No gap marker is in the shipped text.
- No line survives the rival swap. No hit from `references/ai-tells.md` in the headline, subhead or button.
- Customer phrases in the copy trace to a source. No quote was written or reworded.
- Prices match the price sheet of the same date.
- One action per page. The button says what happens next.
- The piece is live (or handed over with a task), and its prediction and date are in `knowledge/results.md`.
- Anything outside the limit went out only after an OK on the version shown.

## Example

Invented, for illustration only.

Task: "/commute gets 3,100 visits a month from search ads and converts at 2.1%. Rewrite it."

Diagnosis: mode (a), generic draft. Ads promise "bike repair without the shop visit"; the page opens with "Welcome to
a smarter way to maintain your ride". The message breaks between the ad and the page. Copy is the problem.

Spine (commuters): Reader: rides to work four or more days a week, no spare bike. Problem, in a customer's words: "I
lose two days every time it goes into the shop." Promise: your bike is repaired while you are at work. Proof: A1
same-day repairs (ops log, Q3 2026); A3 loan bike if we miss; plan price from the sheet dated 2026-11-02. Objection:
"What if it is not ready?" Action: book a pickup.

| | Before | After |
|---|---|---|
| Headline | Welcome to a smarter way to maintain your ride | Your bike, fixed while you're at work |
| Subhead | Seamless, hassle-free servicing for modern cyclists | We collect it from your office by 10 and bring it back by 5. If we miss, you ride home on our loan bike. |
| Button | Get started | Book a pickup |

Gate notes: "seamless" and "hassle-free" failed picturable and the swap. "By 10" and "by 5" are A1's terms.
`[NEED: may we name the two office buildings we already serve? asked Dana, ops]` kept out of the shipped text.

Shipped 2026-11-18 inside the limit (existing page, allowed claims). Prediction: 2.1% to 2.8% over 28 days (about
3,100 visits). This is a before-and-after read, not a test: the page has too little traffic for one.
