# Commands for a settings change

Use these instead of sending a human to a settings page. Commands use the requester's rights directly.

- The commands are `hub api`, `hub bot place|go-live|model|access|owners|pause|resume`, `hub automation update <id> --enable|--disable`,
  `hub human add`, `hub tool add|update`, `hub credential request|set|list|grant|revoke|import|delete`, `hub computer list`,
  `hub bot archive|restore`, `hub bot copy|update-from-original|suggest-to-original`, `hub skill copy`,
  `hub agent pair approve|decline`, `hub slack channel add|list|remove|import`.
- Routines are automations. Find a bot's with `hub automation list --owner bot:<slug>`. Change one with `hub automation update <id>`: `--enable`, `--disable`, or `--file` with the fields to change (`hub automation guide` has the format).
- Slack channels: "let the Setup bot read #customer_success" is `hub slack channel add '#customer_success' --reader <bot>`,
  then say so in one line. Only an owner or admin may do it, so a member is told who to ask.
- If the product truly cannot do it, say so in one line and file it with `hub support file "<what they asked, what you tried,
  what the product said>"`.
- Generated access tasks take a title prefix: `hub tool add --title-prefix <prefix>`. Keep requested title prefixes on child tasks
  and notes too.
- Inbox bots may share any Computer. A Computer kept for inbox bots refuses other bots. To change that when asked:
  `hub api POST runners/<id>/inbox-only '{"enabled": false}'` (or `true` to keep one for them).
- Repository access requests use `hub_bot_repos_set` / `hub bot repos`, followed by a read of `effective` to verify the
  requested repositories and read/write grants. Keep Tool declarations separate: they describe actions and do not grant
  repository access. Preserve existing grants unless the requester asked to change them.
- Freeing task worktrees for a person: `hub api DELETE tasks/<task>/links/<link>` as them asks the computer to remove that
  worktree once its bot is idle. Check its open pull requests first. Do not leave it open just because its requester is a
  bot or keeper.
- Changing a tool a bot already has: `hub tool update <tool-id> --bot <bot> --can read,draft,send`. It changes the entry in
  place, as the requester. Never `hub tool remove` and then `hub tool add` to change one: that files a "Remove ... access" task
  nobody asked for.
- Putting a bot's local repository on GitHub, when the team has connected it: `hub bot repo-create <slug> --empty`.

Inputs: the human's chat request for a settings change, and the command it needs from the list below.

Done when: the change is made as the requester and read back, or the refusal is reported in one line with who can make the change.
