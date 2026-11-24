---
name: set-up-the-company
description: "Sets the company up with its owner in their chat, winning first: what winning looks like, the company and its website, the strategy, the chart, each owner's goal under the company's, their first drafts in Flow, and feedback that becomes rules. Use it when the owner says \"Set up my company.\" (the app sends it once their plan is connected) or asks to set the company up from the top."
metadata:
  function: tico
---

# Set up the company

The owner just connected their plan, or asked to set the company up. You run the setup yourself, in this chat, as them.
One short question per turn; their reply starts your next turn. Never a form, never a list of questions.

## Start: read what is there

`tico goal list --all`, `tico strategy show`, `tico team show`. Skip every step
below that the record already answers: a company the setup page made comes with its goals, strategy and chart. Never
ask what is written down.

## The steps, in order

1. **Winning.** Ask: "What does winning look like for you this year?" (an example answer on a second line: "Double our
   wholesale revenue by June."). Their words become the company goal: read it back with a number and a date
   (`tico goal read-back`, `playbooks/assistant-chat.md`, "Goals") and save it on their yes. Everything after this
   serves it: say so in one line.
2. **The company.** Ask: "Who are you? Your company's name and website." The strategy is the Strategy Owner's: hand it
   their words, `tico task create --owner strategy-owner --title "Draft the strategy from <website>" --message "<their words, quoted>, toward <the company goal>" --request-id turn`.
   It reads the website and drafts how the company wins.
3. **The strategy.** When `tico strategy show` has the draft, say in two lines how the company wins (its summary and its
   bets) and that the Strategy Owner asks them to confirm it in Flow. A strategy draft or bet is never agreed in words.
4. **The chart.** `tico team show`: name the two or three owners the winning goal needs first (a
   growth goal: the Growth Manager and its channel owners). On their yes, Agent Ops hires them:
   `tico task create --owner agent-ops --title "Hire the owners for <goal>" --message "<the seats, and the goal they serve>" --request-id turn`.
5. **Goals that ladder up.** For each owner, one goal that serves the company goal, read back together in one
   `tico goal read-back` and saved on their yes. No owner acts before its goal is saved.
6. **First drafts.** Say that each owner drafts its first work toward its goal, and the drafts wait for them in Needs you
   (`tico needs-you show`): one decision at a time in Flow, Send, Edit or Later.
7. **Feedback becomes rules.** When they say how something should always go ("Reply to good reviews without asking
   me"), add it in their words: `tico rules add --kind internal --text "<the rule>"`. Say it once: "Every agent follows
   it."
8. **Done.** One line: "You're live. Open any page and I'll show you around it the first time." Then stop.

## When to stop

- They say later, not now, skip or stop: "OK, we'll pick it up when you're ready." and stop. "Set up my company."
  again starts from what is still missing.
- A reply that does not answer: ask once more in other words, then go on to the next step.

## Never

- Ask about keys, sign-ins, computers or models: their plan is connected, and Agent Ops raises the rest.
- Send, post, spend or reach anyone outside the team. A draft is a draft until their own yes in Flow.
- File setup questions as tasks with asks, or have more than one question open.
