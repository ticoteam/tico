---
name: build-a-letter-from-records
description: Prepare an employment letter or verification in which every fact comes from a named record, the letter carries only what the type allows, and a human signs and sends it.
---
# Build a letter from records

When to use: when a task asks for an employment confirmation, a verification, a reference letter or a salary letter.

Steps:
1. Read the request: who the letter is for, what it is for, and what the requester wants it to say. Check the letter type in
   `knowledge/letters/<type>.md` and its signer.
2. Name the record for each fact the letter needs: title, dates, employment status. Each one must come from a record named on the
   task, with its date. If a fact has no record, leave it out and say so on the task.
3. Include only what the type allows. Dates and title are the default. Leave out any reason for leaving, a salary, a performance
   note or a conduct note unless a human wrote that line for this letter.
4. Check the letter against the policy on verifications in the handbook. If the request asks for more than policy allows, stop and
   ask the HR owner with `hub task ask <id>`, one question.
5. Mark the letter "Prepared, waiting for signature", with the signer's name and the date it was prepared. Do not sign it and do
   not send it.
6. Attach the letter and its records list to the task. The weekly check counts the days each letter has waited.

Checks before you finish:
- Every fact traces to a named record with a date.
- No reason for leaving, salary, performance or conduct detail is in the letter unless a human wrote it.
- The signer is named, and the letter is marked as waiting for signature.
- Nothing was sent. Any reply to a verification request stays a draft, unless the task asks and a granted Tool allows it.

Example (generic, invented): An employment confirmation for a reference with a start date of 2024-03-04 and the title "Support
Specialist" from the HR record of 2026-09-27. Salary is not asked for and is left out. The letter is marked waiting for the HR
owner's signature, prepared 2026-09-28.
