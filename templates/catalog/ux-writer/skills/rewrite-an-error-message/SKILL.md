---
name: rewrite-an-error-message
description: Rewrite an error message so it names what happened, gives the fix or the next step in plain words, avoids blame and generic phrases, and suggests a correction when one is known.
---
# Rewrite an error message

When to use: each error string in a copy table, and each error a weekly review flags. Read it before you propose a
rewrite, and cite the rule it follows from `knowledge/ux-writing-standards.md` or `knowledge/error-rules.md`.

Steps:
1. Write what the person did or what the system could not do, in one plain sentence. Leave out the code.
2. Write the fix in the next sentence: what to change, or the next thing to do. If the fix is not known, say what the
   person can do now, such as trying again or quoting a reference. Check with the Product Manager before you promise a
   fix the product does not make.
3. Remove blame ("you entered"), filler ("oops", "unfortunately"), and the words "invalid", "valid" and "please".
4. Replace any generic phrase. "This field is required" becomes the name of the field and what it needs: "Enter the
   class name."
5. If the screen already shows the format or the example, do not repeat it in the error. If it does not, state the
   format once.
6. If a correction is known, offer it. WCAG 3.3.3 asks for the suggestion unless it would put security or purpose at
   risk, so a known valid choice is listed or named.
7. Read it aloud. If a listener cannot say what to do next, rewrite it.

Checks before you finish:
- The string names the problem and the fix or the next step.
- No "invalid", "valid", "please", "oops" or blame.
- No generic phrase, and no bare code.
- The format or example appears once, not twice.
- Any suggested correction is one the product can actually make.

Example (generic, invented):
- Before: "Invalid input. Error 40012."
- After: "The end time is before the start time. Set an end time after 10:30."
- Before: "Please enter a valid email."
- After: "Enter an email address, such as name@example.com." (The example appears only here; the field label does not show one.)
- Before: "This field is required."
- After: "Enter the class name."
