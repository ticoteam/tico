---
name: run-an-ai-answer-panel
description: Ask the tracked buyer questions in the AI assistants the owner uses, record whether the team is named, cited and retrieved with dates, and compare with earlier weeks without changing the question set.
---
# Run an AI answer panel

When to use: each weekly visibility check, or when a person asks whether an assistant mentions the team.

Steps:
1. Open `knowledge/prompts.md`. Use its questions word for word, in the order given. Do not add, remove or reword a
   question in this run. A change to the set is a proposal to the owner, with its reason.
2. For each assistant the owner uses, start a new chat with no earlier history and ask each question once. Record the
   assistant, its model or mode if the interface shows it, and the date and time.
3. For each answer, record three results separately:
   - named: the team's name appears in the answer text;
   - cited: a link to one of the team's own URLs appears;
   - retrieved: where the tool shows which pages it fetched, record those pages.
   Also record the competitors named and the third-party URLs cited.
4. Quote, exactly, every phrase where the team is named, and every statement that is wrong. Give the assistant and date
   with each quote.
5. If an assistant is unavailable, or needs a login the bot does not have, record "not checked" with the reason.
   Never record it as "not named".
6. Count only the answers you read: "named in 4 of 9 checked answers". Do not turn a count into a percentage over answers
   you did not check.
7. Compare with the last block in `knowledge/prompts.md`. Report movement only where the same question moved in the same
   direction in more than one week or in more than one assistant. A single change is reported as an observation.
8. Write the results as one new dated block in `knowledge/prompts.md`. Never overwrite the previous block.

Checks before you finish:
- Each question has a result, or "not checked" with a reason.
- Named, cited and retrieved are separate columns.
- No question was added, removed or reworded in this run.
- Every quote carries its assistant and date.
- The report says that an assistant's answer is what it said that day, not a fact about the team.

Example (generic): "Question 2, asked 2026-10-26 in two assistants. Named in assistant A, quoted: '...'. Not named in
assistant B. Cited URL: the team's comparison page, in A only. Previous block 2026-10-19: not named in either. One week, so
it is reported as an observation."
