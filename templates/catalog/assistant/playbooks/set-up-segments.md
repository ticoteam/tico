# Set up segments

The owner asks you to set up their segments (jobs.md 52). It is their chat, so you act as them. A segment, a budget, a
teammate tag and a new bot are cards they tap: nothing changes until they do. A goal is saved only on their plain yes to
a read-back. You never write the strategy draft: its revision is theirs and hidden from you. You draft the words here,
and they write them on the Strategy page.

1. **Ask first.** Three to five short questions in one message: who buys, what they pay for, which group the company
   serves badly today, who already runs it, and which one to start with. Then read the record: `hub strategy show`,
   `hub segment list` (what the company plays in now), `hub market segment tree` (the market) and
   `hub finance segment-review` (customers with no segment yet).
2. **Propose three to five segments**, one card each, after their answers:
   `hub assistant propose --summary "Make <name> a segment" --method POST --path /api/v2/segments --body '{"name":"<name>","who":"<one line: who it is for>"}'`.
   Leave out `color` and the next free one is taken. A market segment of the same name joins the company's.
3. **Draft each segment's strategy in this chat**: its summary, and only the sections it changes (serve, sell, proof, now,
   know, how, not). Ask them to strike or confirm each one. Then say: write it on the [Strategy](#/strategy) page, in the
   segment's part, and publish there. Do not write it yourself.
4. **Goals per segment**: follow job 34. Put each goal's `segment_id` as `segment/<slug>` in the read-back. Save only on
   their plain yes.
5. **Budgets per segment**: one card each. An existing budget: `hub assistant propose --summary "Budget for <segment>: $<N> from <date>" --method POST --path /api/v2/finance/actions --body '{"kind":"budget_update","payload":{"budget_id":"<id>","amount":<cents>,"effective_from":"<YYYY-MM-DD>","segment_id":"segment/<slug>"}}'`.
   Amounts are in cents, as in job 47. A new budget: `hub finance act help` lists the fields of `budget_create`; add
   `segment_id` the same way.
6. **Owners and tags.** Suggest one owner bot per segment from `hub team show`, or a new one. A new bot is a card:
   `hub assistant propose --summary "New bot <name>, owner of <segment>" --method POST --path /api/v2/bots --body '{"display_name":"<name>","description":"<one line>","reports_to":"<manager slug>","model":"<model id from hub bot model>","effort":"<effort>"}'`.
   Its context length is a second card once the bot exists, on a later turn: read its `revision` with `hub bot show <slug>`,
   then `hub assistant propose --summary "Context for <name>: <tokens>" --method POST --path /api/v2/bots/<slug>/compact-at --body '{"compact_at":<tokens>,"expected_revision":<revision>}'`.
   A teammate's tags are a card too: read them with `hub segment members`, then
   `hub assistant propose --summary "Tag <name> with <segment>" --method POST --path /api/v2/segments/members --body '{"member":"bot:<slug>","segments":["segment/<slug>"]}'`.
   Never move a bot yourself.
7. **Finance Manager.** Find its slug in `hub team show`, then
   `hub message send <slug> "Tag each customer with the segment it belongs to, from hub finance segment-review, against these segments: <names>. Put the ones you are unsure of in one list for me." --needs-action`.
   Later, read `hub finance segment-review` for the unsure ones and show them to the owner as one list.
8. **Weekly note.** Ask once whether they want a weekly note for each segment. On yes, make one routine per segment as job 46
   does, owned by the segment's first face in `hub segment list`. Say where the note lands.

Reply in one short message: how many cards are waiting in their chat, the strategy lines to write on the Strategy page,
and that nothing changes until they tap. No undo line: nothing has changed yet. Each card can be declined in their chat.
