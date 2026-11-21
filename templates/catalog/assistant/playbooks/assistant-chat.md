# In a person's chat: cards, clearing a queue, goals, keep going

Procedures for a person's chat. The rules are in `AGENTS.md`; this page only adds the steps.

## Cards
A `confirm_required` refusal: the server refused a write that can be proposed, and made no card. Propose the same
operation yourself: `tico assistant propose --summary "<what happens on confirm>" --path <path> --body '<json>'`, then tell
them a card is in their chat and what happens if they confirm. Never say a card exists before you have proposed it.
Several changes are ONE card: `tico assistant propose` with `/api/v2/assistant/batch`, one summary with counts.
Inbox routines follow **Routines from chat** in `acting-for-them.md`.

**Never proposable, even when the server would confirm it** (the NEVER list in `AGENTS.md`, the team's assistant rules,
and an OK to send, spend or publish): no card. Give one sentence and the link: sign-ins and people
`[Settings](#/settings/people)`, tokens and servers `[Settings](#/settings)`, credentials
`[Credentials](#/tools/keys?show=credentials)`, an OK its task, a support report `[Message Tico support](#/support)`. A
Confirm card from BotOps is theirs to tap; never try to confirm it for them.

## Clearing a queue

"Clear my queue", "leave less than N", "get rid of the old stuff", "why are there so many": do it, do not hand it back.
A target ("less than N", "I still see N") counts Needs you, not their tasks: reach it now, then recount.
1. Read the record: `tico needs-you show` for the count. It is Needs you's count, never a count of the tasks they own. Lead with its count line exactly as it prints, e.g. "8 need you: 4 tasks, 4 asks". Then name the real decisions and the noise to clear (done tasks, test pings, per-release docs tasks), each linked. Never recount or argue. Ids per kind: `playbooks/jobs.md`, "Clearing to a target".
2. Group by kind, source and age: "11 old publish OKs from before the reset", "6 questions from Support".
3. Act per kind as `playbooks/jobs.md`, "Clearing to a target", step 3: close by id, one `tico task close` each; send back stale OKs; answer bots' questions. No card for any of this. A close by filter waits for one yes (job 12a). "Already done" ≠ "nothing to close".
4. For each big group, `tico origin show <task id>` to find what made it, then stop the source (`playbooks/jobs.md`, jobs 30 to 33): an automation update or pause, a rule, or a `botops` task for a bot's own code.
5. Leave what needs their judgement (a question only they can answer, an OK they did not name) and say why. Once they said those items don't need them, none do: close or send them all back (AGENTS.md, Authority). When they say how many to keep, or ask you to pick the important ones, keep that many and close the rest in this turn.
6. Reply with counts before and after, what was stopped, the real decisions left with links, and "say undo to put any of it back".

"Why is this here?" with no ask to stop it: answer from `tico needs-you show --limit 100`: its count per kind and its items grouped by the bot that asked, even when other lookups fail. Give the cause in one line first. Then the top sources with counts, which items are real decisions, and the offer to stop. Never say the count can't be explained, never call it a fault, never page through every task. No "likely" or "probably". Do not act: "stop X" or "clear X" is an ask.

## Goals, agreed in words (the owner only)
Only the owner's own chat, for the owner's goals, measures and bets. Anyone else's goal stays a card.

1. **Read the record first.** `tico proposal list --status rejected` (every suggestion turned down, with its reason), then
   `tico proposal list --status rejected --goal <id>`. Never make a suggestion they turned down, in the same words.
2. **Ask at most three short questions, one at a time, before you draft**: what should be true; whose goal it is; what it
   serves (a bet, from `tico strategy show`). Skip any they already answered.
3. **Recommend once.** If the goal has no number or no date, suggest one once, and name the bet it serves. A "no", or no answer,
   keeps the goal as written, and you do not raise it again in this chat.
4. **Read back the whole plan, once.** `tico goal read-back` with every change of the request in one call (kinds
   `goal_create`, `goal_update`, `measure_add`, `target_set`, `measure_remove` or `suggestion`), never in batches. Tico posts
   it as your reply and saves nothing. End the turn there.
5. **Save on their agreement, in any words** ("yes", "sure, go for it", or a yes to some by number). Judge the words: if a
   reply might not be a yes, ask once. A question or "thanks" keeps the plan: answer it, and their later yes saves it with
   no new read-back. A change to the plan is a new read-back (step 4), which replaces the old one. On the yes, save every
   agreed step in that turn, in order, with `tico goal save POST <PATH> '<body_json>' --on-behalf-of turn` (required on this
   command: `turn`). For a new goal's later steps, add `--created-goal <id>`. Then reply once with
   `[title](#/goals/<id>)` links and how to undo, without showing the plan again.
6. **Not agreed.** If a save is refused (`not_agreed`, or the goal changed since), save nothing more. Say in one sentence
   what was saved, with links, and what wasn't and why (after their "stop", that is the whole answer; otherwise read back
   what is left). Never retry the save.
7. **A "no" to your recommendation** is recorded at once, with no read-back: `tico goal save POST /api/v2/goal-proposals/turned-down
   '<json>' --on-behalf-of turn`, the body being the suggestion and `reason` its one-sentence reason from their words. Say it is noted.
8. **Refused by design.** A colour, done or dropped, archiving, a KPI reading, the company Vision, strategy drafts and bets, and
   recoveries are not agreed in words. If a read-back would include one, Tico refuses the whole read-back and saves nothing.
   Say why in one sentence. A colour and its reason, the vision, strategy drafts and bets go to the Strategy Owner as the
   routing table in `AGENTS.md` says; the rest (done or dropped, archiving, a KPI reading, recoveries) is their click on
   the Goals page.
9. **If the command is missing** on this Tico, file a card with `tico assistant propose` for the goal change and say so in one sentence.

## Keep going

Only when they are working through their goals with you: they asked where they stand or what to do next, or they just did
one of the items you gave them.
1. Finish their ask first. If they say they did an item, confirm it in one line; do not re-read or re-list their goals.
2. End with ONE line naming one thing: `Next: <thing> for [<goal>](#/goals/<id>). Do it now?` The thing is the next item
   from `skills/where-we-stand/` that is theirs to do (an item you already gave them and they have not done comes first).
3. A "yes" to it is their ask: do that one thing, as them, under the usual rules.
4. "That's enough", "stop", "later", "thanks, done for today": reply with one short sign-off ("Good. Talk tomorrow."). No
   recap, no link, no question, no open item. Offer nothing again in this chat.
- No Next line after an ordinary chore or question (a task list, an undo, a lookup, a how-to).
- Never follow up on your own outside a chat.

**Done when:** the person has a few lines with links to what was done and how to undo it, or a card is in their chat with one
line on what happens if they confirm. A goal change is done when its read-back is posted or its saves are answered.
