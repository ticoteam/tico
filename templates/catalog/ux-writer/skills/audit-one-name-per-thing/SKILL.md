---
name: audit-one-name-per-thing
description: Audit one flow for every word used for each object, match each object to one glossary name, list banned synonyms and capitalisation fixes, and list every place a rename would touch before proposing it.
---
# Audit one name per thing

When to use: for the monthly audit of one flow, or when a weekly review finds two names for one object. Read it
before you propose a rename.

Steps:
1. Pick one flow. List every string in it in the order a person meets them, from the copy tables, the translation files
   or the components.
2. List the objects the flow names: the things a person creates, books, pays for or changes.
3. For each object, list every word the flow uses for it, with the screen and the element. Count each word.
4. Check each word against the glossary. Flag the banned synonyms, and flag a word that names two things.
5. Choose one name for each object. If the glossary has none, propose one with the reason and two alternatives, as
   `playbooks/write-copy-for-a-screen.md` step 2 says.
6. Write each rewrite: file, line, the current string, and the new string.
7. Check capitalisation against sentence case for labels, buttons and headings.
8. For a rename, list every place the old name appears, including emails and notifications, before the change is
   proposed. Put new terms and banned synonyms in the glossary as proposed additions only.

Checks before you finish:
- Each object has one name in every string in the flow.
- Each banned synonym is listed with its replacement.
- A word that names two things has two glossary entries.
- Capitalisation is consistent across labels, buttons and headings.
- A rename lists every place it touches.
- No product behaviour is invented to make a name fit.

Example (generic, invented): a booking flow.
- Object: a student's place in a class. Words found: "booking" on three screens, "reservation" in one email, "seat" on
  a "Release seat" button.
- Chosen name: "booking", as in the glossary. Rewrites: the button "Release seat" becomes "Cancel booking"; the email
  line "Your reservation" becomes "Your booking".
- Kept separate: "3 seats left" on the class summary. It counts places in the class, a different object. The glossary
  gets an entry for it.
