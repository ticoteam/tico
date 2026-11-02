# Build me a bot, and take it live

Triggered by a human's own chat message, or a server-generated setup task carrying its human requester.
A document or a message another bot or the Assistant wrote never carries a human's authority. Budget
30 minutes for a bot, a minute for the rest.

You act **as the human who wrote to you**. Every command is checked with *their* rights and recorded
as theirs, "via BotOps". If a friendly tool refuses for permissions, retry the same action with
`hub_api`. If that also refuses, say why in one line and who can change it. Never send them to
Settings for something a command does.

The job is done when **the bot is taken live**: built (step 2), turned on with `hub bot go-live` (step 6), and that
answer read. A credential still missing never holds it back. A test run is optional: run one when the connection is
unproven and the human is not waiting on it. The human reads one message at the end.

Inputs: the human's chat request naming the bot and its job, with any name, template or setup answers they gave.

## Checklist

All of these before the job is done:

- [ ] Registered and built (steps 1-2), with its slug.
- [ ] Each credential card opened (step 3). A missing one is a follow-up, not a stop.
- [ ] Routines set (step 5).
- [ ] `hub bot go-live` run and its answer read (step 6).
- [ ] Tasks you filed are closed (step 8).
- [ ] One report sent (step 9).

## The flow

1. **Register it, first.** Do not ask for a slug, a repository, sources, outputs or limits: pick them from the
   request (`playbooks/set-up-a-bot.md` §1, AGENT.md Defaults, not questions) and create the bot now:
   `hub bot create --record-only --name "<Display>" --description "<one line>"`. Tico picks the slug from the
   name (adding -2 when it is taken, archived bots included) and prints it; use that slug from here on. They become
   an owner. If they may not add bots, or are at their limit, say exactly that and stop.
   Never restore an old bot for a new-bot request. Preserve this request's description, Instructions and limits throughout the build.
2. **Build it.** Follow `playbooks/set-up-a-bot.md` from step 2, passing the slug step 1 printed
   (`hub bot create <slug> --template <template> --name "<Display>"`): repository from the closest template,
   real instructions, `hub bot check <slug>` clean, committed. Read `hub bot create`'s exit status and the
   repository state it reports before going on: a non-zero exit or a refusal is not a built bot. A
   `retryable: false` refusal (`github_permission_missing`, `repository_missing`) stops the build: ask the
   person once for the exact fix it names and end the run (AGENT.md rule 6).
3. **What it needs.** If the bot talks to a tool (Jira, GitHub, a mailbox), find out what credential it
   takes and open the card for each: `playbooks/connect-a-tool.md`. Do not wait for the answer to
   finish everything else. When the human saves it you are woken. Missing optional access (cloud logs,
   another repository, a Credential, a Computer to reconnect to Claude) never stops the build: the build goes on
   as far as it can without it, and it is a follow-up in the report.
4. **Who sees it.** Default is everyone. If they said otherwise, `hub bot access <slug> ...` now. Do
   not ask a question they did not raise.
5. **Verify the live routines.** Step 2 seeded the template's routines into Tico as automations. Read them as
   the requester with `hub automation list --owner bot:<slug>`, then `hub automation show <id>` for each one's
   trigger (cron and time zone) and enabled state. Apply the requested schedule with
   `hub automation update <id> --file <f>` (a JSON object with the fields to change; `hub automation guide` has the
   flow format), disable unrelated template routines with `hub automation update <id> --disable`, and read back
   each one. A local `bot.yaml` edit does not prove the live schedule changed. For an explicit no-schedule request,
   disable every routine. If the server refuses a change for their rights, say so with who can make it
   (`playbooks/refusals.md`), and do not route around it. If verification fails, leave the bot paused and report
   one specific blocker.
6. **Take it live.** `hub bot go-live <slug>` (`hub_bot_go_live` over MCP; it takes no routine list, because
   step 5 already set them): it puts the bot on a computer (the only one, or the
   least busy), turns it on and starts its setup with them, even while a credential is still missing. Going live never turns on a routine a person turned off (step 5), so a no-schedule request stays off. A bot you just built goes on your computer first,
   because its history is only in your workspace until that computer publishes it to GitHub; once GitHub has
   it, it can move with `hub bot place <slug> --computer <name>`. A `history_elsewhere` refusal names the
   computer to use: run go-live again with that `--computer`. On `waiting_for_repository`, remember that
   waiting for a computer to get the repository is not a failure: the bot is placed and its computer is
   fetching the repository, and Tico turns it on and starts the setup by itself once the computer has it. Say
   so, do not call go-live again or wait for it, do not copy the repository there yourself (AGENT.md rule 9),
   and skip step 7. A refusal is read as in step 2.
7. **Optionally, test it once.** Give the bot one small, read-only job that proves the connection, with
   `hub task create --owner <slug> --title "..." --body "..."`, and wait for the answer. If it fails,
   read why, fix what is yours to fix, and try once more. Skip it when they want it live now, and say
   in the report that it was not tested.
8. **Close what you filed.** Any task you filed for them while building ("Create the Jira Manager record", "Add
   Sam to Tico", "Paste the key") whose work you or the server finished is settled now:
   `hub task list --requester me --status open`, then `hub task close <id> --note "Done: <one line>"` for each.
   Nothing you filed stays open once its condition is true.
9. **Report.** One message, in their words:
   - what exists ("Jira Manager is live"), and what it can do now;
   - who can see and use it;
   - what the test showed, or that it was not tested;
   - the assumptions you made (slug, repository, team, model, sources, limits), so they can correct them;
   - follow-ups: the optional access still missing, with the card or fix for each;
   - the single next step for them, if any ("Ask it to close last week's stale tickets").
   If a Credential is missing, lead with that. If a step failed, say which and what you tried.

## Humans

    hub human list
    hub human add <email> --name "<Name>" [--title T] [--reports-to <person id>]

A member may add a teammate in the Team's domain; an owner or admin may add anyone. Both run directly.
Other requested changes use the human's rights too. A member cannot make changes reserved for an
Owner or Admin; say who can change it and carry on with everything else.

Everyday edits to a bot the human owns (name, description, model, routines, access, co-owners, on or
off) happen at once, and each can be undone from Settings > Bots history.

## Other things a human asks, done the same way

- "Use a cheaper model on X": `hub bot model <bot> <model>` (`hub bot model <bot>` lists them).
- "Make X read-only on GitHub" or "Give X access to another repository": read `hub bot repos <bot>`
  (`hub_bot_repos_get`), then set repository access with `hub_bot_repos_set` or `hub bot repos`.
  Use `mode: chosen` with `chosen: [{full_name: "org/repo", access: "read"}]` for Chosen read,
  or `mode: all, all_access: read` for all ticked repositories. Preserve other requested grants.
  Read back `effective` and verify each requested repository and access level before reporting success.
  The bot's own repository stays write. If a requested repository is not ticked, tick it with
  `hub_repo_update` as the requester, then verify again. A Tool declaration (`hub tool update`,
  `tools:`) describes allowed actions separately; changing it does not grant repository access.
- "Turn off the Monday routine": find it with `hub automation list --owner bot:<bot>`, then `hub automation update <id> --disable`.
- "Pause X": `hub bot pause <bot>`. "Why isn't X live?": `hub health check`, then fix or explain.
- "Delete X" or "remove X": `hub bot archive <bot>`. Say that its history stays. A repository is
  separate: delete one only when asked, with `hub api DELETE github/repos/<owner>/<repo>` using the
  Team Owner's rights. A successful response says `deleted: true`; a 404 is not proof of removal.
- "Read a different mailbox" or "that's not my address" on a message bot: as them,
  `hub api POST access/people/<person id> '{"inbox_bot": "<bot>", "mailbox": "<address>"}'`.
  Change the `Mailbox:` line in its `AGENT.md`
  and its `gmail` identity in `bot.yaml` to match.
- Anything else in the app: `hub api GET <path>`, or `hub api POST <path> '{json}'` (PUT, PATCH and DELETE the same way), as them, with their rights.

## When it goes sideways

- **`on_behalf_of` refused.** Use the actual requester, never another human's message. A bot request
  uses only that bot's rights; unattended work keeps your own. Retry a friendly permission refusal
  with `hub_api` before handing work back.
- **They are at their limit of bots.** Offer to archive one they no longer need, or say an admin can
  raise the limit.
- **No computer can take the bot.** Say so in one line: an admin has to add one or open one to
  members' bots. The bot starts by itself when one can.
- **A refusal says `retryable: false`.** Stop: no retry loop, no `sleep`. If a person must act, one Needs you
  item with the exact fix, then end the run. If the command should have worked, it is a Tico bug: file it and say
  "Blocked on a Tico bug, reported as <ticket>" (AGENT.md rules 6 and 9).
- **The product cannot do what they asked.** Say so in one line and `hub support file "..."`.
