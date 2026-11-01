---
name: ask-with-recommended-answers
description: Ask the owner a short set of questions, each with a recommended answer and a default, inside a stated time box. Keep one answers table, rewrite a changed answer in place, and do not hand work on until the owner says go.
---
# Ask with recommended answers

When to use: when a task needs more than one answer from the owner, or when a missing fact would change a score, a status or a route.

Steps:
1. Decide whether to ask at all. A reversible detail is assumed and marked "assumed" in the output. Anything irreversible (money, customer data, a public promise, spending) waits for an answer.
2. Write three to five questions at most. Each has one line on why it matters, a recommended answer, and the default that applies if the owner does not reply.
3. Send them in one card with `hub task question`, or as one message on the task with `hub task ask <id>`. State the time box: "I will proceed with the defaults on <date>, and mark them assumed."
4. Keep one answers table on the task or in `state.md`: question, answer, date, who answered. When an answer changes, rewrite its row. Do not add a change section.
5. When the time box ends, proceed with the defaults and list which answers were assumed at the top of the output.
6. Do not hand work to engineering until the owner says go. Record the go with its date.

Checks before you finish:
- No more than five questions.
- Each question has a recommended answer and a default.
- The time box is stated, with its date.
- Assumed answers are labelled as assumed wherever they are used.
- There is one answers table, and changed answers are rewritten in place.
- No work was handed on without a recorded go.

Example (invented, for illustration): "Three questions, answers by Thursday 09:00, or the defaults apply and are marked assumed. 1. Does the quarter's outcome serve a goal in Tico? Recommended: yes, the booking-time goal. Default: yes, assumed. 2. Is the calendar sync fix still committed for this quarter? Recommended: yes, moved to next sprint. Default: yes, assumed. 3. Can the waitlist research start with the two studios already interviewed? Recommended: yes. Default: yes, assumed."
