# What is broken? ("Tell me issues to solve", "status")

Triggered by a human asking what is wrong, what to fix, how things are, or why a bot is not working.
Budget 5 minutes. You answer with a short prioritised list and you have already fixed what you may.

Inputs: the human's question about what is wrong or how things are, and the bot in it if they named one.

## 1. Look

    tico health check

It lists, most urgent first, what is wrong with the bots this human may see, each with the one
command that fixes it: a bot with no computer, a computer that is offline, failing runs, a credential a bot
needs, setup that never finished, a bot paused or stopped. For "why isn't X live?" read X's lines, then
`tico bot status list` and `tico run list <bot> --since 24h` for the last thing it did.

## 2. Fix what you may, now

Do each fix as the human, with their rights. Do not ask first.

| Issue | You do |
| --- | --- |
| Not on a computer | `tico bot place <bot>` |
| Turned on but never finished setting up | `tico bot go-live <bot>` |
| A planned bot Health says is still being set up | report it as ready to turn on; when they asked you to take it live, the section below |
| Paused | `tico bot resume <bot>` (unless they paused it on purpose: then say so and ask whether to resume it) |
| A credential a bot needs | open the card: `playbooks/connect-a-tool.md` |
| No repository on its computer | Clone the named original repository into the exact path in Health, on that computer, using its person's git access. Follow the steps below. |
| Failing runs | `playbooks/diagnose-a-failed-run.md`; fix instructions in the bot's repository if that is the cause |
| A Hermes bot that is not reporting in, or archived and still reporting | `playbooks/connect-a-hermes-profile.md` |
| A computer is offline | nothing you can do: say which one, and that its bots wait for it |
| A computer low on disk | never delete by hand: the section below |
| A computer is holding work to update | nothing to fix: it updates by itself once the turns Health names end. Say which turns, read it once and end the run (`AGENTS.md` rule 10); never watch it or stop a turn for the person |
| Waiting on a usage limit, and the person says the plan is renewed | `tico api POST /api/v2/bots/<bot>/limit/retry` (clears every bot limited on that runtime and computer) |
| Stopped after refusing something | `tico api POST /api/v2/bots/<bot>/quarantine/clear` only after you read why |

A Tool with too few verbs (a mail bot that cannot send) is changed in place with
`tico tool update <tool-id> --bot <bot> --can ...`, never removed and added again.
For missing GitHub repository access, use `tico_bot_repos_set` / `tico bot repos` and verify
`effective` before reporting success. Tool declarations and repository grants are separate.

A fix the server refuses for their rights is not a failure: name it in the list with who can do it.

### A planned bot Health says is still being set up

Only when the requester asked you to take it live (`AGENTS.md`: turning a bot on is theirs to ask for).

1. Read `tico bot status history <bot>` first. If its newest reason says a person kept it off, do not turn it on: say so in one line.
2. Otherwise run `tico bot go-live <bot>` as the requester, and read the answer:
   - `state` active: it is live. `placed` true: it is on a computer. `setup_started`: its setup has begun.
   - `state` `waiting_for_repository`: its computer is fetching the repository, and Tico turns it on by itself once the computer has it. Do not run go-live again.
3. Name the routines that are on from `routines` in the answer (`on` true), and only those: going live turns on the routines
   its template came with, except one a person turned off or changed. Say none is on when none is.

### A computer low on disk

The computer cleans up by itself when its workspace runs low, and a cloud machine Tico hosts grows its disk by itself.
Read Health's disk row: it names what the computer's last clean-up freed. Never delete anything by hand, on any
computer, not even in `/tmp` or a cache: you cannot see which files are open, which locks are held or which tasks are
finished.

Ask a person only when a computer is still low after its own clean-up, and only about what is outside its safe list
(Docker images, files owned by root or another user, a bot's repository, a worktree holding unsaved work, package
caches in the home folder): one Needs you item naming the computer and what Health shows.

### A missing repository

When a human asks you to fix it, do the clone yourself if you run on the computer Health names.
Read the bot's definition (`tico api GET bots/<bot>`) and, for a branch, its `shared_from` original's
repository. Check the destination first: use an existing sibling checkout with that repository
name if it has `AGENTS.md`; never overwrite files or create an empty repository for a branch.
Otherwise run Health's `gh repo clone <owner/repo> <path>` command with the person's own git
access. If `gh` is unavailable or signed out, use `git clone <repository-url> <path>` with the
computer's existing git credentials. Do not borrow BotOps' repository-scoped GitHub App token.
Check that `AGENTS.md` exists, then run `tico bot check <bot>` and `tico health check` again after
the computer reports readiness. Do not turn a missing `AGENTS.md` into new instructions for a branch.

If the computer is someone else's or you cannot reach it, say which computer needs the exact
command. Never clone onto your own computer as a substitute. If the original repository address
is unknown, find it from the original's definition or a matching local checkout's git remote;
if neither has it, ask the owner for that address. Only an independent bot whose repository
does not exist on GitHub uses `tico bot repo-create <bot> --empty` and the publish steps in
`playbooks/set-up-a-bot.md`.

## 3. Answer once

One message: one list in Health's order (most urgent first; do not move a fixed item up), at most five lines. Each line is a
plain sentence: what is wrong, then "Fixed" when you fixed it, or what the person must do. End with the single most
useful next step. If nothing is wrong, say that in one line and how many bots you checked.
No internal words or names: "Setting up", "your computer", "the Jira credential", variable names such as `BILL_API_TOKEN`, slugs, ids.

The job is done when that one message is sent.
