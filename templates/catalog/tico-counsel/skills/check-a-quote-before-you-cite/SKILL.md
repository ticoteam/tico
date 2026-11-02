---
name: check-a-quote-before-you-cite
description: "Before a suggestion cites a quote and its section, check that the quote is copied character for character from the named document and version, is a whole clause of at least 20 characters, and sits under that section."
---
# Check a quote before you cite it

When to use: before each `hub rules add` that carries a `--quote`, and when a write is refused because its quote is not
in the document.

Steps:
1. Copy the quote from the text `hub file read` returned for this version, not from memory or from a summary. Keep
   punctuation, capitals and numbers exactly as printed.
2. Take a whole clause: from where the sentence starts to where it ends. It must be at least 20 characters. A fragment
   such as "within 30 days" does not show the duty; take the clause that holds it.
3. Find the heading above the clause and use its text as `--section`. If the document has no headings, use the
   paragraph number if it has one, or else the first five words of the paragraph.
4. Check the file id and the version against the task's `Document:` line. Never cite another document, or another
   version.
5. Check the clause holds an obligation that binds us (`skills/list-the-obligations-in-a-document/SKILL.md`).
6. If a write is refused because the quote is not in the document, count it as dropped. Do not rebuild the quote from
   memory; go on to the next obligation.

Checks before you finish:
- The quote matches the document text character for character.
- It is a whole clause, at least 20 characters.
- The section is a heading or label that appears above the clause.
- The file id and version are the ones the task names.
- Every dropped quote is counted for your last line.

Example (invented, for illustration): version 2, section "Data", quote "The Customer shall delete the Supplier's data
within 30 days of the end of the agreement." The quote is the whole sentence, copied as printed, with its section named.
