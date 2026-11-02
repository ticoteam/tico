---
name: "answer-a-developer-question"
description: "Turn one public developer question into a checked reply: the fix, one line of why and one doc link, tested against the version the asker uses."
---
# Answer a developer question

When to use: before you draft any public reply to a developer question, in a forum, a discussion, a Q&A tag or a chat.

Steps:
1. Restate the question in one line, with the SDK version, language and runtime if the asker gave them. If the version is
   missing, ask for it in the reply and say which version you tested against.
2. Find the answer in the docs with `hub doc search` and `hub doc ask`. Open the page and confirm the section you cite
   exists and says what you will say. A link that opens is not the same as a link that answers.
3. When the docs and the question disagree, check the code path the answer depends on, in the SDK source or its tests.
4. If there is a failure, reproduce it with the version the asker uses. Run any code in the reply with
   `skills/test-a-code-sample/SKILL.md`.
5. Check the reply against `knowledge/do-not-say.md`. Remove any roadmap date, price, security detail or named customer.
6. Write the reply: the fix first, then one line of why, then one doc link. Keep it under about 120 words. No apology
   and no "great question".
7. Decide the destination. Post only when the task asks, a Tool allows it and mail sending is on in Tico. Otherwise keep
   the reply as a draft with its channel, thread link and exact text.
8. If you cannot check the reply, say what you could not verify and send the question to the named engineer instead.

Checks before you finish:
- The reply names the SDK version and the date you ran it, or says "not run" and why.
- Every link opens, and the cited section says what the reply says.
- No key, token or account detail from the question appears in the reply. Tell the asker to rotate any key they pasted.
- No promise of a feature, a fix, a date or a price.
- The reply answers the question the asker asked, not a neighbouring one.

Example (invented, for illustration): A forum post reads "401 on every webhook, SDK 3.4.1, Flask". The reply reads: "The
signature is computed over the raw request body. If the framework parses the JSON first, the bytes change. Read the raw
body and pass it to `verify`. Docs: Verify signatures. Run against SDK 3.4.1 on Python 3.12, 2026-10-01." It names the
version, the run and one link, and says nothing about when a fix will ship.
