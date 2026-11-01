---
name: "write-release-notes-for-users"
description: "Write the customer-facing release note for one change in plain present tense: what the user can now do or what no longer goes wrong, who notices it, what they must do, and where to find it, with no internal names and no pull request titles."
---
# Write release notes for users

When to use: for each change that a user will notice, when the customer-facing section of the draft is written or revised.

Steps:
1. Name the user-visible result in one sentence, from the user's side: "You can now...", "Reminders are sent...", "Times stay right
   after a clock change." Start with the user's words, not the feature's name.
2. Say who notices it: all users, admins only, users on one plan or platform. Leave out people who do not notice.
3. If the user must do something, say what, in one step: where to click or which setting to change. If nothing is needed,
   say so only when a user might expect to do something.
4. If the change is breaking, put the instruction first and the reason second. Give the date it takes effect if one applies.
5. Remove internal words: ticket numbers, service names, library names, "refactor", "migration", a developer's name. Keep the
   product's own feature names exactly as the product shows them.
6. Read it as a user who has never seen the code. If they would ask "so what?" or "where?", add the one missing clause.
7. Keep it short: one or two sentences for a fix, a short paragraph for a feature with a setting to find.

Checks before you finish:
- Each note starts with what the user sees or can do.
- No pull request title is pasted as the note.
- Breaking changes say what to do first.
- No internal name, customer name, security detail or unreleased plan appears.
- Every note links its pull request in the changelog section, not in the customer text.

Example (invented, for illustration): instead of "Refactor reminder scheduler and add channel enum (#433)", write "Clients on a
waitlist can now get reminders by text message. Turn it on under Settings, then Reminders."
