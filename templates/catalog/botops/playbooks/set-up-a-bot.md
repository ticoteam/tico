# Set up a bot

Triggered by a task that asks for a new bot, or by `playbooks/build-me-a-bot.md`. One bot per task,
no schedule. Budget 30 minutes.

The outcome is a repository in {{company_name}}'s workspace that passes its readiness check and holds
instructions the owner recognises as what they asked for. When a human asked you in chat, you then
take it live (`tico bot go-live <slug>`). A seat on the company chart (its task says so) you take live
too, with no person step. For any other task nobody chatted, the note tells the owner the one thing to read
before they turn it on, and they do.

Inputs: the task (`tico task show <id>`): the bot's name, its template, its slug if it is already registered, and any reviewed instructions or setup answers.

Done when: `tico bot check <slug>` passes or its warnings are explained in the note, and the note from section 6 is on the task. A chart seat is also active: Tico refuses its task's done until it is. Any other bot stays off unless the human asked you to take it live.

---

## 1. Read the task

The task names the bot, the template it comes from, and, when the bot is already registered, its slug, and it carries the
owner's reviewed instructions and the setup answers.

    tico task show <id>

A task titled `Build a bot: <name>` carries a name and a plain-language job
instead of a template. Pick the closest template in the templates, and write `AGENTS.md` for that job.

If any of those is missing, choose it and go on (AGENTS.md, Defaults, not questions):

- **Slug:** never ask anyone for one, and leave it out of `tico bot create`: Tico picks it from the name (else the
  template's name), lowercase with hyphens ("Projects Manager" is `projects-manager`), adds -2, -3
  when it is taken or reserved, and prints it. Use the printed slug from then on and name it in the report. Pass a
  slug only when the bot is already registered: the task names one, or you registered it with `--record-only`
  earlier in this run (build-me-a-bot step 1). Leaving it out then makes a second bot with -2.
- **Display name:** the name they used; otherwise a short name for the job.
- **Template:** the closest one to the job.
- **Repository owner:** the GitHub organization they named, otherwise the connected one.

Name every choice you made in the task note, so the owner can correct it.

## 2. Create the repository

    tico bot create --template <template> --name "<Display>"        # add the slug when the bot is already registered (§1)

That prints the slug it picked and materialises `$HUB_WORKSPACE/bot-<slug>` from the template, fills the team's names
into the placeholders, writes `knowledge/company.md` from the setup answers, and seeds the
template's `routines:` into Tico as the bot's first routines (the result lists their ids). When a
human asked for the bot in chat, it also registers the bot with the server as them and makes
them an owner (`playbooks/build-me-a-bot.md`); for a task with no human behind it, the owner registered
it already.
Read what it produced before you change anything: the template is a starting point, not the
answer. Use `tico bot branch <bot>` for shared instructions and lessons on your own computer; `tico bot copy <bot>` starts an independent bot from the original repository's contents.
From here on a routine is changed in Tico with `tico automation update <id>` (its id is in `tico automation list`), not by editing the file.

## 3. Put the real instructions in

If the task carries the owner's reviewed instructions, replace `AGENTS.md` with them. Keep the
section order the template uses, so `## Role`, `## Owns` and `## Boundaries` are still
where a reader and the readiness check expect them, and keep at least one real bullet under
`## Owns`.

If the task carries no reviewed instructions, tailor the template's `AGENTS.md` to the setup
answers instead. Three things have to be true and specific in it:

- what the team does, in the words the answers used;
- what arrives where, so the bot knows which queue, inbox or source is its input;
- which work the bot owns, named concretely. Sending to outsiders stays off until requested.

Cut every line that is not true for this team. A vague line left in is worse than a missing one,
because the bot will act on it.

A setup answer that is still missing blocks only the step that needs it. Never write "if setup is
incomplete, stop" into the instructions or a routine: name the step that waits (publishing, sending
to outsiders, a rollout, a payment) and say that everything else goes ahead, with the bot asking the
missing question once, naming the person, while it works. A Release Owner without rollout access
still builds, checks and writes up the candidate; only the rollout waits. Do the same pass over `bot.yaml`: the display name, the labels,
and a schedule only if the owner asked for one.

If the instructions include a `Mailbox: <email>` line (message bots), replace every `{{mailbox}}` in
`bot.yaml` with that address. That is the mailbox this bot is assigned; do not invent one.

The server has already named the bot as that person's message bot and recorded the mailbox when the bot was
added (the person is the one whose email on the roster is that address). If `tico health check` still says the
bot's Gmail tool has no message bot, name it yourself as the person who asked:
`tico api POST access/people/<person id> '{"inbox_bot": "<slug>", "mailbox": "<the Mailbox: address>"}'`
(a card for their click). That link is the one thing that lets its runs on a Docker computer read the mailbox.
Changing a message bot's mailbox later is the same call with the new address.

The instructions a new bot starts with already say how it publishes what it makes (the "Publishing
your work" section that comes with every template): reports and exports in `reports/` or
`artifacts/` are listed on its page after a run, `tico file publish <path>` lists one at once,
`tico file link <url>` lists a Google Doc, Sheet or Notion page it creates, and `tico file import
s3://bucket/key` copies an S3 object. Keep that section when you rewrite the role. A bot whose work
should not be listed sets `files: {publish: []}` in `bot.yaml`; one that writes deliverables
elsewhere names the folders, `files: {publish: [reports/, deliverables/]}`.

## 4. Check it

    tico bot check <slug>

Fix everything it reports as a failure: an `AGENTS.md` still identical to the
template, an empty `## Owns`, a `name:` that is not the slug, a schedule that would be refused. A
warning can stand if you say in the note what it is and why it is acceptable now. A credential the bot
needs and does not have is yours to ask for: open the card (`playbooks/connect-a-tool.md`), never
send the human to a settings page, never ask them to paste it in words.

## 5. Commit

Commit the new repository with a one line message that says what it is, for example
`Set up <slug> from the <template> template`. Nothing in the commit may contain a credential value.

## 5b. Its GitHub repository, if GitHub is connected

`tico bot create` already asked for the bot's empty private repository `<org>/bot-<slug>` (its answer says
so under `github`); with no GitHub connected it skips this and the bot stays on its computer. For a bot set
up some other way, create it with:

    tico bot repo-create <slug> --empty

Do not push it yourself: the bot publishes its own history. The bot's repository link (Settings, Bots) has to be `<org>/bot-<slug>`
(a bare `bot-<slug>` also resolves to the connected organization). Its owner sets it there, or you set it
for the human who asked in chat with `tico bot update <slug>` (it takes their rights); say so in the task note. The bot's
first placement is your computer, where its history is: the runner there sets `origin` to that repository and
publishes the history with the bot's own token, and never forces: if the repository already holds different
history it stops and Health says so. Once GitHub has it, the bot can move to any computer. You do not push other bots'
repositories. If `tico bot create` failed with `github_permission_missing`, a person must act: the app
was not given permission to create repositories. Do not work around it or retry (AGENTS.md rule 6). Finish
steps 3 to 5 in the local repository anyway, then say plainly in the task note that the bot waits for its
GitHub repository and that the owner has a Needs-you task with the fix it names; tell the person who asked
once, and do not file another ask. Tico finishes the repository on its own once the owner acts, and the bot
stays local until then. Never create a repository for a slug other than this bot's.

## 5c. Close what you filed for a human

If you filed a task for a human about this bot ("Create the record", "Add Sam", "Paste the key") and you have
now done that work yourself, or it is already true, close it with one line: `tico task close <id> -m "Done:
<what>" --version N`. Find them with `tico task list --requester me`. A task left open after the work is
done sends the human to do something that is finished.

## 6. Finish the task

    tico task done <id> -m "..." --version N

The note says, in this order:

1. the repository path;
2. what you changed from the template, in a sentence or two;
3. the readiness result, with anything still warning;
4. the one thing the owner should read before it goes live, usually the `## Boundaries`
   section or a gap the answers did not fill.

For a chart seat, take it live first: `tico bot go-live <slug>`. When it answers `waiting_for_repository`, the
computer has not reported the repository yet and Tico finishes going live by itself: snooze the task a few minutes
(`tico task snooze <id> --until <time> -m "waiting to go live" --version N`) and say done once `tico bot show <slug>` says active;
your done ends your snooze. If it is still waiting after 30 minutes, say so on the task once, naming what Health says, and leave
it for the owner.

Going live turns on the routines its template came with, except one a person turned off or changed. In the note, name the
routines that are on from `routines` in `tico bot show <slug>` (`on` true), and only those: never say one is on because the
template has it.

Otherwise, unless a human asked you in chat to take it live, the bot stays as it is (still being set up) until its
owner turns it on.

## When it goes sideways

- **The slug already exists, including archived bots.** Do not overwrite a repository or restore
  the old bot for a new-bot request. Pick a fresh slug (§1) and name it in the note. Restore only when explicitly requested,
  and use the current request's scope rather than an older bot's Tools, repository or Routines.
- **The template does not fit the job the answers describe.** Set it up from the closest template
  anyway, say plainly in the note which parts of the instructions you had to write from nothing, and
  suggest what a better template would contain.
- **The check fails on something outside the repository**, such as a runtime that is not installed
  or a profile with no sign in. That is the owner's to fix. Record the exact failing line on the
  task and finish; do not work around it.
