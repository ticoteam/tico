---
name: check-a-security-answer-against-its-source
description: Test a security, privacy or compliance answer against a documented source before it reaches a buyer, and sort it into documented yes, workaround, not supported, or no documented answer.
---
# Check a security answer against its source

When to use: before you answer a security, privacy or compliance question in a call, questionnaire or RFP, and before you repeat an answer someone else gave.

Steps:
1. Write the buyer's question in their words, with the deal and the date.
2. Find the documented answer with `hub doc search` and `hub doc ask`. Note the document, its date or version and the section.
3. Check the document is current against the changelog. A document older than a release that changed the answer is a question for its owner, not an answer.
4. Sort the answer into one class: documented yes (quote the line); yes with a workaround (write the workaround and its source); not supported today (say so, with the source's date); no documented answer (name the security owner from the team docs and the date the buyer needs).
5. Do not turn a narrow control into a broad claim. "Encrypted in transit" is not "encrypted at rest" unless the document says both.
6. Draft the reply for the requester. It goes out only when the task asks for it, a granted Tool allows it and a person has turned mail sending on in Tico. Otherwise it stays a draft.
7. Record the question and its answer in the deal's technical note, so the next questionnaire reuses the sourced answer.

Checks before you finish:
- Every answer has a document, a date and a section.
- No answer comes from memory, from an old questionnaire alone, or from a chat message without a document.
- A "no documented answer" goes to the named owner in the same task, with the buyer's deadline.
- No detail about another customer's environment appears in the answer.

Example (invented, for illustration): "Buyer asks how long audit logs are kept. Security overview, section 'Logging', dated 2026-08-02: logs are kept for the period it states. The buyer's longer period is not supported. Answer: the documented period, with its date. The security owner is asked before any longer period is offered. Logged in the deal note."
