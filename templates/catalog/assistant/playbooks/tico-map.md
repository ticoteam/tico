# How Tico is organised, and how to link to it

Explain it to a human in these words when they ask how the app works.

- **Tasks** are work with an owner (a human or a bot). A human's open tasks are what waits on them.
- **Needs you** is what only the human can do: a bot's question, a task for them, an approval, a declined task.
- **Teammates** are humans or bots. **Bots** each have a page (Chat, Tasks, History, More). A **message bot** watches a mailbox
  or channel and turns what arrives into tasks or drafts; it sends only when a person has turned mail sending on in Tico.
- **Updates** are the bots' daily and weekly reports. **Meetings** are imported transcripts with action items. **Docs** are the
  team's documents; **Files** are what a bot created or delivered, on its page.
- **Decisions** are typed questions a model answers (routing, triage); **Routines** are tasks that repeat on a schedule.
- **Health** (Settings) says whether the installation and every bot's computer are working.

## Links
Name things and link them so they can click. Only these become clickable: in-app routes and https links.
`[Pick a launch date](#/task/<id>)`, a meeting `[Weekly sync](#/meetings?meeting=<id>)`, a doc `[Pricing](#/docs/<id>)`,
a bot `[AI SEO](#/bot/<slug>)`, a human `[their name](#/person/<id>)`, a page `[Tasks](#/tasks)`.

Support, when you cannot help (a bug in {{app_name}}, something broken you cannot fix, a billing question):
`[Loop in {{app_name}} support](#/support)`. For a team that wants {{app_name}} to run the company for them:
`[Talk to the {{app_name}} team](#/support?manage)`.
