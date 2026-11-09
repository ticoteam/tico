---
name: answer-a-policy-question
description: Answer a community question about refunds, prices, terms or what the team will do, quoting the current policy document with its version and never offering an exception.
---
# Answer a policy question

When to use: a thread asks about a refund, a price, a plan limit, a term, cancellation, data retention or an exception. Also when a member quotes something a team member said in chat.

Steps:
1. Ask the docs: `hub doc ask "<the question, in the member's words>"`. Take the answer and its citation: the document, its version or date, and the section.
2. Check the citation is the current version. If the document has changed since the answer was written, say so to the owner before you draft.
3. If a person in chat or on a task said something that conflicts with the document, do not pick one. Record both with their dates in `knowledge/community.md` under source gaps, and ask the owner which one stands.
4. Draft the reply with the policy in the first sentence, then the steps the policy gives, then the citation. Do not add a promise the policy does not make.
5. Never offer an exception, a discount, a credit or a gesture. If the member asks for one, say it has gone to the person who decides, and create a task for that person with the thread link.
6. If the docs do not cover it, report a doc gap to the Librarian (`hub task create --owner librarian`) and mark the reply as needing a person.

Checks before you finish:
- The citation names the document, its version or date, and the section.
- The reply says what the policy says, not what the member hopes.
- No exception, credit or timing promise appears.
- Any conflict between sources is recorded for the owner, not settled by you.

Example (generic): "Refunds: the policy document (version 1, section 'Refunds', read 2026-09-20) says refunds are available within 14 days of the first charge. Reply: 'Refunds are available within 14 days of the first charge. Steps: ...', with the link. The member asked for an exception for a late request. It went to the billing owner with the thread link; the reply makes no promise."
