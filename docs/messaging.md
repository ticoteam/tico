# Message bots

> **Beta**: message bots (mail and calendar) work today and may still change. See [Beta features](support.md#beta-features).

For how a message reaches a bot, see [Steer, queue or defer](using-tico.md#steer-queue-or-defer); for deferred
messages and tasks, [Leave work for the next run](using-tico.md#leave-work-for-the-next-run).

The **Message bots** section in the sidebar is the directory for repeating email and Slack work;
there is no separate messaging index or search page. Choosing a mailbox or channel opens its bot
directly. The left side shows covered sources, published instructions, status and recent Slack channel checks. The right side is a list of example messages and actions. Email and
Slack thread viewers open the original message context, and Slack messages link back to Slack.
The source chips can widen or narrow the examples to the bot's other mailboxes and channels.

A message bot's schedules and other triggers are automations: **Automations** on its page opens the Automations page
searched by its name, with each automation's runs and **Run**. **Pause bot** uses the same API and authorization as the bot's
page.

Coverage is read from the live bot config and roster for Gmail, and from
the Slack channel list (Tools > Slack) for Slack readers. A bot may cover many sources. Source assignments
still follow the tool and registry configuration that grants actual access; this view does
not create a Gmail permission or change a Slack channel reader.

The server stores the last `AGENT.md` published by each assigned runner in
`bot_agent_instructions`. A runner sends a file on its first successful heartbeat and whenever
its timestamp or size changes. Until then, the bot view shows the published instructions summary. An old
runner may continue publishing only personal message bot instructions; the page uses that copy as a
fallback.

The mail tool's cloud snapshot contains message labels and matched rule IDs. Those are
shown as message evidence. Its detailed local audit remains on the Mac; a source message with
no bot-specific `hub/handled/<bot>` or `hub/triaged/<bot>` label is never attributed to the bot.
Slack digests have exact event IDs, so a check opens the messages delivered to that bot. Slack
replies come from `slack_posts` with their delivery state. Slack channel message content is
restricted to the owner in Message bots; everyone else's email view stays within their existing
mailbox access. The backend enforces these rules for every detail and thread endpoint.
The gateway publishes the verified Slack workspace URL so message links open the exact Slack
thread. Before that URL is available, links open the channel.
