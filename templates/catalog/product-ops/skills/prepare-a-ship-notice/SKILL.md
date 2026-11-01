---
name: prepare-a-ship-notice
description: Prepare one ship notice per account that asked for a request now released, each checked against the released behaviour and the account's current record, and account for every account on the list.
---
# Prepare a ship notice

When to use: a request in the ledger is marked shipped, or its release note is out, and someone needs the list of who
to tell. Read it again when an account on the list closes or a contact changes.

Steps:
1. Confirm the request is shipped: the release note or milestone is closed and the behaviour is live for accounts.
   If it is only merged, in beta or scheduled, it is not shipped. Record that and stop.
2. List every account on the row, with where and when it asked. Each account gets one line on the list, and each line
   ends in one of three states: ready, needs contact, or dropped (with the reason).
3. Check each account in the CRM (read). If it is closed or merged into another account, drop it and say which.
4. Check the contact. Use a named contact with a current address. If there is none, or the address bounced, mark
   "needs contact". Do not guess an address.
5. Write one notice per account, 40 to 90 words. Refer to that account's own request, in the words it used if the
   source has them. Say what now works, where to find it, and what did not change. Make no promise about later work
   and no date beyond the release.
6. Check each claim in the notice against the release note or a tested behaviour. Cut any claim with no source.
7. Put the list on the task for the owner: account, contact, state, and the notice text. Nothing is sent.
8. Note on the ledger row: "notice drafted" and the date. Set "notified" only after a person confirms the send.

Checks before you finish:
- One notice per account, not one per row.
- Each notice refers to that account's own request and makes no claim the release note does not support.
- Every account on the row is ready, needs contact, or dropped with a reason.
- No notice is marked sent by this bot.
- The release note link is on the task.

Example (generic, invented):
- Row 14, "Export the class roster to a spreadsheet", released 2026-10-08. Accounts asked: A (ticket, 2026-09-14),
  B (call notes, 2026-08-30), C (email, 2026-07-19).
- Account A: active, contact current. Ready. Notice: "You asked for a way to take the class roster out of the app as a
  spreadsheet, for a substitute teacher. Export is now under Reports, then Class roster. Nothing else about how lists
  print has changed." (42 words)
- Account B: contact bounced in August. Needs contact. The notice is drafted, not addressed.
- Account C: closed in September. Dropped, reason "account closed 2026-09-02".
- Each notice quotes or paraphrases the account's own words and claims only what the release note says.
