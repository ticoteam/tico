---
name: write-bot-instructions
description: "Write or repair a bot's AGENT.md so it names the team, what arrives where, the work the bot owns and the lines it never crosses, in a form the bot can follow with no other context."
---
# Write a bot's instructions

When to use: when you write a new bot's `AGENT.md` from a template or from the owner's reviewed text, or repair one after
a failed run or a wrong answer.

Skills propose changes to systems only when the task asks for them and a granted Tool allows it.

Steps:
1. Name the outcome the bot must reach, in one sentence, and the person who owns it. If you cannot name either, ask once.
2. Keep the template's order and headings. `## Role`, `## Owns` and `## Boundaries` stay where the readiness check
   expects them (`playbooks/set-up-a-bot.md`, step 3).
3. Role: whom the bot works for, what the team does in the owner's words, and what arrives where: the queue, inbox or
   source that is its input.
4. Owns: concrete bullets for the work the bot does itself. Each bullet should be something you could check the bot
   did. Keep at least one real bullet. Cut any line that is not true for this team, because the bot will act on it.
5. Boundaries: keep the lines that always hold: the never-lines, sending off until a person turns it on, credentials
   only as granted, and the rules for tasks filed for humans. Copy them from the template. Do not soften one.
6. Method: the steps the job takes, in order, with the point where a person must act and the shape of the output.
   One outcome per run.
7. Put rare rules in a playbook that `AGENT.md` names, with the moment to read it. The file should stay short enough to
   read in one sitting.
8. Read the whole file as the bot would, with no other context. Mark each sentence it could act on wrongly, and fix or
   cut it.
9. Run `hub bot check <slug>`, fix each failure, and commit with one line that says what changed.

Checks before you finish:
- Every line is true for this team. A guess is written as an open question in `knowledge/`, not as a rule.
- No credential value, customer name, personal address or account id appears in the file.
- The bot cannot send outside the team unless a person turned sending on. `outbound_send` in `bot.yaml` stays false here.
- A rule that appears in two places says the same thing in both.
- Every playbook the file names exists, and the file says when to read each one.

Example (invented): The owner's request is "An invoice checker for the billing team. It reads the billing inbox, matches
each supplier invoice to its purchase order, and drafts questions to the billing lead."
- Owns: "Matching each supplier invoice to its purchase order before the Friday run."
- Boundaries: "Drafts questions to the billing lead. Never sends to a supplier."
- Method: "1. Read new invoices in the billing inbox. 2. Match each one to its order by number and amount. 3. Write the
  mismatches to `reports/`, one line each, and list them on the task."
A candidate line, "Keeps the supplier list up to date", has no source the bot can read, so it is cut.
