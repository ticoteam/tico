---
name: check-a-doc-against-the-code
description: Check each checkable claim in a doc page (commands, paths, option names, defaults, UI labels, click paths, code samples and links) against the code or UI at the commit the page describes, and record each result.
---
# Check a doc against the code

When to use: before you call a page accurate, before you ship a draft, and before you report a sentence as wrong.

Steps:
1. List every checkable item on the page: commands and their flags, file and folder paths, option and field names, default
   values, UI labels, click paths ("Settings, then Reminders"), code samples, and links.
2. Set the commit. Use the commit the page describes, or the head of the default branch if the page describes the
   current product. Write the SHA in your notes.
3. Check commands and flags. Read the command's definition in the code (its argument parser or its help text), or run
   it in a clean checkout if it needs no credentials. Mark a command "not run", with the reason, if it needs a live account.
4. Check names and defaults. Find each option or field in the code. Compare its name, type and default with the page.
5. Check UI labels and click paths. Find the label string in the UI source at that commit, or in a screenshot from that
   build. If you cannot reach the UI, mark the item "to verify" and name who can confirm it.
6. Check paths and links. Confirm each path exists at that commit. Confirm each internal link resolves and each anchor
   exists. Confirm each external link opens, and that the page it opens supports the sentence it is attached to.
7. Check code samples. Run each sample against the version the page names. If it cannot run here, mark it "not run" and say why.
8. Record each item with its result: ok, wrong, or to verify, plus the file and line that show it.

Checks before you finish:
- Every item on the list has a result, and none was skipped silently.
- Each "wrong" result quotes the page sentence and the source line it conflicts with.
- The commit SHA is recorded.
- Labels, paths and defaults are the items most often wrong, so they were checked first.
- Anything you could not confirm is "to verify", never "ok".

Example (invented, for illustration): The page "Set up reminders" tells the reader to choose "Email" under
"Notifications". At commit 3f9a1d2, the UI label reads "Email and text" and the section is "Reminders", not
"Notifications" (`web/src/settings/Reminders.tsx`, line 42). Result: wrong, two items (section name and label). The link to
`docs/reminder-times.md` does not exist at that commit: wrong. The step that needs a sandbox account: not run, because
there is no account on the computer.
