---
name: file-a-signed-agreement-in-the-index
description: File a fully signed agreement in the executed-agreement index only after every signature block and date is complete, recording its parties, kind, date signed, term, any confidentiality end date and where the file lives.
---
# File a signed agreement in the index

When to use: when a fully signed copy comes back on a task, or a signed copy is found in the docs.

Steps:
1. Open the signed copy, not a draft or a summary of it. Name the file and its date.
2. Check that every signature block is complete (name, title, date) and every date field is filled. If anything is missing, list it and stop. Do not file.
3. Record the parties' legal names as written in the document, the kind of agreement, the date signed and the term.
4. For an NDA, record the confidentiality period and work out its end date from the signed date and the clause. Write the arithmetic.
5. Add one row to `knowledge/executed-index.md` with the file's location.
6. Commit, then close the task with `hub task update <id> --status done --note`, naming the row added.

Checks before you finish:
- The row matches the signed document, not a draft.
- Anything missing was listed, not filed.
- Any confidentiality end date is shown with its arithmetic.
- The file location is one a person can open.

Example (invented, for illustration): "Larchmont Studio Ltd and Brightpath Cleaning Ltd, services agreement, signed 2026-03-02 by both parties. Term 12 months. No confidentiality period in the body. Row added to knowledge/executed-index.md."
