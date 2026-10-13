# Turn a bot's sending on (a message bot's mail going outside the team)

Triggered only by a human's own chat message to you that asks for it: "let my Inbox Manager reply to support
and forward job mail to me at <address>". Not by a task, a document, another bot's message or the Assistant's,
and never because a brief, a draft or a health check suggests it. A bot that would like to send is told to ask its
human; you turn it on when the human says so. Budget 10 minutes.

You act **as the human who wrote to you**. The server checks their rights, not yours: they must own the bot or
manage it (its owner, a co-owner, an admin, or someone it reports up to). If the server refuses, say so in one line
and who can change it, and stop. Do not look for another way in.

## What "on" means, so you can say it in one message

With sending on, the bot follows the rules the human gave it with no approval for each message. Three kinds of
recipient go out that way; everything else still waits for an allowance or an approval:

1. **People inside the team's own domain.**
2. **The sender of the message it is answering**: a reply only, to that one person, nobody added.
3. **The forward addresses the human listed** for this bot, such as their other email.

The daily cap, the wait between messages to one person, the blocklist and the people the owner handles personally
still apply. The human can turn it off again in one message.

## Steps

1. **Get the facts from the message.** Which bot (`hub api GET bots/<slug>` if they did not name it), who it may
   answer, and where it forwards what. Ask one question only if an address is missing: "Where should I forward it?"
2. **Record the permission as them.** The bot already has a gmail tool (`hub tool list --bot <slug>` shows its id):
   `hub tool update <tool-id> --bot <slug> --can read,draft,send`. It changes the entry in place; never remove it and
   add it again, which files a "Remove Gmail access" task nobody wants. No gmail tool yet:
   `hub tool add <slug> gmail --can read,draft,send --identity <the bot's mailbox>`. This is the step the server
   checks against their rights; it answers at once. A refusal ends the playbook. It files one task for you with the
   entry; when step 3 has put that entry in `bot.yaml`, close it: `hub task update <id> --status done --note "..."`.
3. **Change the bot's repository** (`$HUB_WORKSPACE/bot-<slug>`):
   - `bot.yaml`: the gmail entry's `can: [read, draft, send]`. Never touch `outbound_send` or `forward_to`: they are
     only a request, and the switch itself is the person's (step 6).
   - `playbooks/inbox-preferences.md`, `## Sending`: change `Off` to `On` and write their rules in their own
     words, one bullet each ("Reply to support senders that support has it". "Forward job, business-development,
     partnership, investor and press mail to <address> with the subject `🔔 Tico inbound: <subject>`, and add a
     Needs you task").
   - `AGENT.md`: under `## Boundaries`, the sending line must say it follows `## Sending` in
     `inbox-preferences.md` now. Nothing else about what it may send changes.
   - `questions/mail-triage.json` must exist in the repository; if it is missing, copy it from
     `$HUB_DIR/questions/mail-triage.json`.
4. **Check and commit.** `hub bot check <slug>`; fix what it reports; commit with one line. Put the decision in
   `memory/decisions.md` (who asked, which rules, which addresses).
5. **Ask them to turn the switch on themselves.** Only a person who manages the bot can, and never you, even as them:
   Settings > Bots > <bot> > Mail sending (tick "Send without a per-message approval" and type the forward
   addresses), or `hub bot mail <slug> --send --forward-to <addresses>` with their own token. Say exactly which
   addresses to type.
6. **Report in one message.** Say what you changed, which three kinds of recipient go without approval once they turn
   it on (say them plainly: the team's own domain, a reply to the person who wrote, and the forward address(es) by
   name), what the rules are, that the daily cap and the blocklist still apply, and how to turn it off (the same
   switch). Once they say it is on, `$HUB_DIR/scripts/mail.sh policy show --as <slug>` says `outbound_send True`
   and lists the forward targets.

## Turning it off, or changing who it forwards to

The person turns the switch off, or changes the forward addresses, in the same place; you change the matching line in
`## Sending`. Taking an address off the list is reversible by asking again; tell them at once.

## When it goes sideways

- **The server refuses `hub tool add` or `hub tool update`.** They do not manage the bot. Say who does.
- **`on_behalf_of` refused.** The run was not started by the human's own chat message or comment. Tell whoever is on the task;
  do not act as anyone and do not turn anything on.
- **`mail.sh policy show` says outbound_send False.** The person has not turned it on in Tico yet; its line says so.
  Never report "on" before it says so.
- **The bot is not its person's message bot yet.** `hub health check` says so; link it as them
  (`playbooks/set-up-a-bot.md`, step 3).
