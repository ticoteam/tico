# Set up segments

The owner asks you to set up their segments (jobs.md 52). It is their chat, so you act as them. A segment, a strategy text, a teammate's tags, a customer's segment and a weekly note are cards they tap: nothing changes until they do. A goal is saved only on their plain yes to a read-back. A new bot is a BotOps task (job 29). Budgets and the customer tags are the finance bot's tasks, and it applies its own approvals.

The finance bot is the bot whose template is `finance-lead` (usually Head of Finance); `hub team show` lists each bot with its template. If none is installed, say so once, and leave budgets and customer tags to the owner on the Finance page.

Each turn waits for the owner's answer, and ends with one reply.

**Turn 1: read, then ask.** Read the record first: `hub strategy show`, `hub segment list` (what the company plays in now), `hub market segment tree` (the market), `hub growth numbers` (sign-ups by month) and `hub finance segment-review` (customers with no segment yet). Then ask three to five short questions in one message: who buys, what they pay for, which group the company serves badly today, who already runs it, and which one to start with.

**Turn 2: the segment cards.** After their answers, propose three to five segments, one card each. Nothing else in this reply.
- `hub assistant propose --summary "Make <name> a segment" --method POST --path /api/v2/segments --body '{"name":"<name>","who":"<one line: who it is for>"}'`. Leave out `color` and the next free one is taken. A market segment of the same name joins the company's.

Reply in one short message: how many cards wait in their chat, and "nothing changes until you tap them." The rest comes on the next turn, once the segments exist.

**Turn 3: drafts and questions, once the segments exist.** Run `hub segment list` and use only the real ids it prints (`segment/<slug>`). Then, in this order:
1. **Owners and tags.** Suggest one owner per segment from `hub team show`. A teammate already tagged `all` gets no card: `all` alone is every segment. Otherwise, one card per teammate. The card replaces the teammate's list, so read the current one with `hub segment members` and send every segment it already has, plus the new one: `hub assistant propose --summary "Tag <name> with <segment>" --method POST --path /api/v2/segments/members --body '{"member":"bot:<slug>","segments":["segment/<existing>","segment/<new>"]}'`. A person is `person:<id>`. Never move a bot yourself.
2. **Strategy drafts.** Draft each segment's strategy in this chat: its summary, and only the sections it changes (serve, sell, proof, now, know, how, not). Ask them to strike or confirm each one. Nothing is saved yet.
3. **Budgets and customers.** Ask the budget amount and the date it starts for each segment, or whether they want you to propose them. Ask whether the finance bot should tag their customers by segment.
4. **New bots.** Ask whether any segment needs a new bot to own it. Ask for its name and its segment.
5. **Weekly note.** Ask once whether they want a weekly note for each segment.
6. **Goals.** For each goal they named (job 34), ask at most three short questions, then `hub goal read-back` with the goal's `segment_id` as `segment/<slug>`. Make it the last call of the turn: the read-back ends the turn.

If there is no goal to read back, end the turn with one short message holding the drafts and the questions.

**Turn 4: on their answers.** Nothing changes until they tap a card, or say yes to a read-back.
- **Strategy.** One card per segment, with only the sections they kept: `hub assistant propose --summary "Strategy for <name>: <keys>" --method PUT --path /api/v2/segments/strategy --body '{"id":"segment/<slug>","sections":{"<key>":"<text>"}}'`. It has no revision: the tap writes only those keys. Then say: publish it on the [Strategy](#/strategy) page, in the segment's part, after you tap the card.
- **Goals.** On their plain yes, save each agreed step as job 34 says. Link each saved goal as `[goal](#/goals/<id>)`, and say "say undo to put it back."
- **Budgets.** One task to the finance bot, with the amounts they gave, or "propose" if they said so: `hub task create --owner <finance slug> --title "Propose segment budgets" --body "Propose one budget per segment, each with segment_id segment/<slug>: <segment: amount in cents and start date, as the owner gave them, or proposed by you>. Update the budget where one exists, create it where none does; hub finance act help lists the fields. Each one goes through your own approvals."`
- **Customers.** If they said yes: `hub task create --owner <finance slug> --title "Tag customers by segment" --body "Tag each customer with the segment it belongs to, from hub finance segment-review, against these segments: <names>. Put the ones you are unsure of in one list for the owner."`
- **New bots.** One BotOps task per new bot, as job 29 says: `hub task create --owner botops --request-id turn --title "New bot <name>, owner of <segment>" --body "<their words>. Model: <id from hub bot model>. Effort: one that hub bot model lists for that model. compact_at: one of 100000, 150000, 250000, 400000, 600000, 1000000. Segment tags: segment/<slug>, and any others they named."`. `hub bot model <any bot slug>` lists the models and the efforts each one takes.
- **Weekly notes.** One card per segment, owned by that segment's owner bot. A segment whose owner is a new bot waits until BotOps has built it: `hub assistant propose --summary "Weekly note for <name>" --method POST --path /api/v2/automations --body '{"name":"Weekly note: <name>","owner":"bot:<owner slug>","trigger":{"type":"schedule","cron":"0 9 * * 1","tz":"<their IANA zone>"},"steps":[{"id":"run","type":"bot_turn","bot":"<owner slug>","prompt":"Each Monday, write a short note on <name> (segment/<slug>): what moved in its strategy, bets and teammates. Send the note to the top face of the segment: its first lead, else its first face."}]}'`. Take the zone from `hub settings find timezone`; with none, leave `tz` out and say that it runs in UTC.

Reply in one short message: how many cards wait in their chat, that the finance bot has its tasks, and that BotOps builds any new bot. Nothing changes until they tap. Each card can be declined in their chat.

**Later turn: the finance bot's customer task is done.** Read `hub finance segment-review` and show the owner one list: the customers the finance bot tagged, and the ones it left unsure. For each customer the owner places, one card: `hub assistant propose --summary "Put <customer> in <segment>" --method POST --path /api/v2/finance/contacts/<id>/segment --body '{"segment_id":"segment/<slug>"}'`, with the `<id>` the review gives. Reply: how many cards wait in their chat, and "nothing changes until you tap them."
