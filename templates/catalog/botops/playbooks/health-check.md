# What is broken? ("Tell me issues to solve", "status")

Triggered by a human asking what is wrong, what to fix, how things are, or why a bot is not working.
Budget 5 minutes. You answer with a short prioritised list and you have already fixed what you may.

## 1. Look

    hub health check

It lists, most urgent first, what is wrong with the bots this human may see, each with the one
command that fixes it: a bot with no computer, a computer that is offline, failing runs, a credential a bot
needs, setup that never finished, a bot paused or stopped. For "why isn't X live?" read X's lines, then
`hub bot status list` and `hub run list <bot> --since 24h` for the last thing it did.

## 2. Fix what you may, now

Do each fix as the human, with their rights. Do not ask first.

| Issue | You do |
| --- | --- |
| Not on a computer | `hub bot place <bot>` |
| Turned on but never finished setting up | `hub bot go-live <bot>` |
| Paused | `hub bot resume <bot>` (unless they paused it on purpose: then only mention it) |
| A credential a bot needs | open the card: `playbooks/connect-a-tool.md` |
| No repository on its computer | Clone the named original repository into the exact path in Health, on that computer, using its person's git access. Follow the steps below. |
| Failing runs | `playbooks/diagnose-a-failed-run.md`; fix instructions in the bot's repository if that is the cause |
| A Hermes bot that is not reporting in, or archived and still reporting | `playbooks/connect-a-hermes-profile.md` |
| A computer is offline | nothing you can do: say which one, and that its bots wait for it |
| Waiting on a usage limit, and the person says the plan is renewed | `hub api POST /api/v2/bots/<bot>/limit/retry` (clears every bot limited on that runtime and computer) |
| Stopped after refusing something | `hub api POST /api/v2/bots/<bot>/quarantine/clear` only after you read why |

A Tool with too few verbs (a mail bot that cannot send) is changed in place with
`hub tool update <tool-id> --bot <bot> --can ...`, never removed and added again.
For missing GitHub repository access, use `hub_bot_repos_set` / `hub bot repos` and verify
`effective` before reporting success. Tool declarations and repository grants are separate.

A fix the server refuses for their rights is not a failure: name it in the list with who can do it.

### A missing repository

When a human asks you to fix it, do the clone yourself if you run on the computer Health names.
Read the bot's definition (`hub bot show <bot>`) and, for a branch, its `shared_from` original's
repository. Check the destination first: use an existing sibling checkout with that repository
name if it has `AGENT.md`; never overwrite files or create an empty repository for a branch.
Otherwise run Health's `gh repo clone <owner/repo> <path>` command with the person's own git
access. If `gh` is unavailable or signed out, use `git clone <repository-url> <path>` with the
computer's existing git credentials. Do not borrow BotOps' repository-scoped GitHub App token.
Check that `AGENT.md` exists, then run `hub bot check <bot>` and `hub health check` again after
the computer reports readiness. Do not turn a missing `AGENT.md` into new instructions for a branch.

If the computer is someone else's or you cannot reach it, say which computer needs the exact
command. Never clone onto your own computer as a substitute. If the original repository address
is unknown, find it from the original's definition or a matching local checkout's git remote;
if neither has it, ask the owner for that address. Only an independent bot whose repository
does not exist on GitHub uses `hub bot repo-create <bot> --empty` and the publish steps in
`playbooks/set-up-a-bot.md`.

## 3. Answer once

One message. First what you fixed ("Put Jira Manager on your computer and turned it on"), then what is
left, most important first, at most five lines, each a plain sentence and what to do. End with the
single most useful next step. If nothing is wrong, say that in one line and how many bots you checked.
No internal words: "Setting up", "your computer", "the Jira credential".
