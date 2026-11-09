---
name: draft-a-how-to
description: Write a how-to page that solves one task for a reader who knows the basics, with a goal title, numbered single-action steps, an exact expected result, and recovery steps for the failures people hit.
---
# Draft a how-to

When to use: when the reader needs to do one task in the product, and a reference page or an explanation would leave them stuck.

Steps:
1. Write the title as the goal the reader has: "Invite a teammate", "Export a report as CSV". Not "Inviting teammates".
2. Write one sentence on when to use the page. Add a short list of before-you-start items: the role needed, the version,
   and what must already exist.
3. Write the steps, numbered, one action each, in the order the reader performs them. Start each step with the action:
   "Open Settings." "Choose Invite."
4. After the last step, write what the reader should see, exactly: the text, the state or the file. For example: "You
   should see 'Invitation sent' under the address."
5. Write the failures people hit, two or three of them, from support notes or the pull request. For each, give the message
   the reader sees and the fix.
6. Link to one next step and one reference page for the options. No more.
7. Keep it short: five to nine steps. If the task needs more, split it into two how-tos and link them.
8. Check it with `skills/check-a-doc-against-the-code/SKILL.md`, and mark any step you could not confirm.

Checks before you finish:
- The title names a goal.
- Each step is one action, in the order the reader performs it.
- The expected result is exact, and it comes from the product, not from the author's memory.
- Each failure step names the message the reader sees.
- Second person, present tense, active voice, sentence-case headings, and no "simply" or "just".

Example (invented, for illustration): "Invite a teammate." When to use: a new person needs access to the workspace.
Before you start: you are an admin. 1. Open Settings. 2. Choose Team. 3. Choose Invite and enter the email address.
4. Choose Send. You should see "Invitation sent" under the address. If you see "This address already has an account", the
person is already a member: choose Add to team instead. Next: Manage roles.
