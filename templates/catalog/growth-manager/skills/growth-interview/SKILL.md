---
name: growth-interview
description: Get Growth's numbers by asking people, not by forms. Use when someone asks you to set up Growth ("Help me set up Growth."), when `tico growth setup` says setup is not done, or on a call about growth. Works out what is missing, asks whoever knows, one question at a time, and saves each answer as it arrives.
metadata:
  function: growth
---
# Growth interview

It is easier to ask someone than to make them fill in a form. You find what Growth is missing, ask
the person most likely to know, and write each answer the moment it arrives.

## 1. Read before you ask
    tico growth setup
    tico growth numbers --months 6
    tico growth channel list
    tico growth source list
    tico strategy show
    tico team show
Never ask what these already answer. A strategy that names the channels, or a finance report with
spend, is an answer.

## 2. What Growth needs, in order
1. The channels customers come from, and which of them already work. A large "don't know" or
   "word of mouth": `skills/ask-how-they-heard/SKILL.md`.
2. Per channel, the last full month: spend and new customers.
3. Revenue per customer per month, and margin.
4. How long customers stay (kept 30, 90, 180 days), and why they leave
   (`skills/learn-why-customers-buy/SKILL.md`).
5. The two or three numbers the team looks at every week, and where they live.
Stop when the first three are in. The rest can wait for the next conversation.

## 3. Who to ask
Start with whoever messaged you. For each fact, ask the person who owns it (`tico team show`):

| Fact | Ask first | Then |
|---|---|---|
| Channels, what already works | founder or owner | whoever runs marketing |
| Spend per channel per month | whoever runs marketing | finance |
| New customers per month | sales lead | founder |
| Revenue per customer, margin | finance | founder |
| How long customers stay, why they leave | support or success | founder |

## 4. How to ask
- One question per message, in plain words, with an example answer: "Roughly what did you spend on
  Google Ads last month, and how many new customers came from it? A guess is fine, for example
  '$4k, about 30'."
- Never ask for a format. Take the answer however it comes: a ramble, a pasted sheet, a screenshot,
  "about 30".
- A guess is welcome. Save it as an estimate. Never hold a number back because it is rough.
- After the first answer, keep going in the same chat until the person stops or the first three
  facts are in. Keep each turn short.
- "I don't know, ask Ana": ask Ana the same question with one line of context
  (`tico chat ask --to <person id> "<Name> said you'd know. <the question>"`) and carry on with the
  person you are talking to. Several people can be asked at once. Someone not on the team: ask for
  a guess and say it is an estimate.
- Never ask more than three questions of someone who did not start the conversation.

## 5. Save as you go
Follow `skills/numbers-from-chat/SKILL.md` after every answer. Reply with one line that says what
you saved ("Saved: Google Ads, October, $4,200 spent, 31 new, estimate"). Kept things go to
`skills/what-already-works/SKILL.md`.

## 6. Finish
When the first three facts are in, say in two lines what Growth now shows and what is still
missing, and who you asked about it. Record who answered what in `memory/decisions.md`. Keeping the
numbers fresh is `skills/keep-numbers-fresh/SKILL.md`.

## On a call
The same steps, spoken: one short question at a time, read back each number before you save it,
and say "Saved" when you have. Prepare with `skills/prepare-a-growth-call/SKILL.md`.
