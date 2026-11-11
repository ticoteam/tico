# The `tico` CLI from a terminal

`tico` is the same command set a bot runs in its turn (`tico task list`, `tico bot go-live`, ... ; `tico --help`), and
every command is one tool of the MCP server (`tico task create` is `tico_task_create`). Rights live on the server: a
command can do nothing the API refuses its credential. This page is the plumbing around the commands: which company
and credential a shell uses, picking fields from an answer, following live events, the website commands, and what
the exit codes mean.

## Which company: profiles

A profile is a company's address and the token to use there. One shell can talk to several companies:

```
tico auth add acme --url https://acme.tico.team --token-file ~/tico/acme_token   # checks the token, then saves
tico auth add beta --url https://beta.tico.team --with-token < beta_token.txt     # stores the token in the file
tico auth list                      # name, address, where each token is; never a token
tico auth use acme                  # the default when no profile is named
tico whoami                         # who you are, the company, and the profile in use (tico auth status says the same)
tico --profile beta task list       # one command against another company; --profile goes anywhere on the line
TICO_PROFILE=beta tico task list    # the same for a whole shell
tico auth remove beta               # forgets the profile; the token itself keeps working until revoked
```

Profiles live in `~/.config/tico/profiles.json` (or the file `TICO_CONFIG` names), mode 600. `--token-file` keeps
only the path and reads the file at each call, so a token file that is replaced keeps working; `--with-token` stores
the token in the profiles file, which `tico` then refuses to read if others can.

Which credential a command uses, first match wins:

1. `--profile NAME` on the line;
2. `TICO_PROFILE`;
3. `HUB_API_URL` and `HUB_TOKEN`: what the runner sets for a bot's turn, and what a person's own script may set;
4. the default profile (`tico auth use`);
5. for `tico sql` and the tool reads only, this Mac's runner credential (`~/.config/tico/runner.json`).

A bot's turn never uses a profile: `--profile` or `TICO_PROFILE` there is refused, and its own token is what it is.

## Personal tokens

```
tico token list              # your tokens: label, created, last used, expires, revoked; never a secret
tico token revoke <id>       # stop one at once (yours; it may be the token you are using)
tico token create --label "release script" --days 30    # a signed-in browser session only (see below)
tico token list --all        # everyone's (owner and admins, signed-in browser only)
```

A token lists and revokes its person's own tokens. Making a token takes a signed-in browser (**Settings > Computers >
API tokens**, or **Connect an external agent**), so a leaked token cannot extend its own life; from a token,
`tico token create` prints that refusal. A token made there is shown once; give it to `tico auth add --with-token`
or save it to a 600 file for `--token-file`.

## Picking fields: `--fields`

Answers are JSON on stdout. `--fields` (anywhere on the line) keeps only the fields named, a dotted path reaching in:

```
tico task show 60e069fc --fields id,title,stage_id,turn.state
tico task list --fields id,title,stage_id        # a list: picked from each item
```

The key of each picked value is the path as typed; a field the answer does not have is `null`. When the answer is an
object holding one list (`{"tasks": [...], "next_cursor": ...}`) and none of the fields is a key of the object, the
fields are picked from each item of the list and its paging keys stay. `--fields` always prints JSON, in place of a
command's text form.

## Following live events: `tico events tail`

```
tico events tail                                  # everything you may read, one JSON line per event, until Ctrl-C
tico events tail --task 60e069fc --fields seq,kind,task.stage_id,task.status
tico events tail --pipeline Engineering --count 20
tico events tail --topics tasks,bots --timeout 300 --after 4120
```

It reads the stream the web app reads (`GET /api/v2/events`, [Live events](custom-frontend.md#live-events)). Each line
is the event's data with `event` (its topic) added; a `tasks` line carries `seq`, `id`, `kind`, `kinds` and `task` (as
the task list shows it: `pipeline_id`, `stage_id`, `status`, `owner_id`, `turn`, ...). The stream ends every fifteen
minutes or when the network drops; the tail reconnects from the last change number it saw, so nothing is missed. A
`reset` line means it fell further behind than the server keeps: read what you are watching in full. `--task` (id,
short id or number) is the server's filter; `--pipeline` (id or name) is applied by the CLI to each task's
`pipeline_id`. `--topics` defaults to `tasks` when `--task` or `--pipeline` is given. Notes about reconnecting go to
stderr. A bot's turn cannot follow the stream; people and their tokens can.

## Websites: `tico browser` and `tico login`

A bot drives a website tool's browser a verb at a time and signs in without seeing the password
([integrations/website.md](../integrations/website.md), [Website logins](credential-vault.md#website-logins)). These
are bot commands: a bot's turn (and BotOps) runs them; a person uses the tool's page.

```
tico browser open <site> [url]          # opens a page on the tool's sites, signed in as your account
tico browser read <site>                # the page as text; each control on its own line with a ref (e12)
tico browser click <site> <ref>
tico browser type <site> <ref> <text> [--submit]
tico browser press <site> <key>
tico browser scroll <site> [--dy N]     # 600 by default; negative scrolls up
tico browser back <site>
tico browser screenshot <site> [--out PATH]   # writes the JPEG (default ./screenshots/<site>-<time>.jpg), prints its path
tico browser download <site> [--out DIR]      # writes the newest download (default ./downloads), prints its path
tico browser upload <site> <ref> <path>       # a file in your checkout, at most 10 MB
tico browser close <site>                     # lets go of the account; the sign-in stays
tico browser needs-you <site> "<line>"        # asks a person on your task to do a step only a person may
tico login fill <site> username|password|code [--ref eN]
tico login copy <site> username|password|code # onto this computer's clipboard, cleared after 30 s
tico login code <site>                        # the current 2FA code and the seconds it has left
```

`<site>` is a site, a page URL on it, or the tool's name or id. Each command waits up to 40 s and is never retried by
the CLI (a retry could click twice). It prints one line a bot reads (the page as `<title> — <url>`, the page text, a
path, a code) and `--json` prints the answer as JSON. Nothing prints a password or a 2FA key. A refusal prints one
sentence with what to do next, in place of the JSON error: for example "Builder is using this account; try again in a
minute.", "Mac mini is offline; try again later, or ask a person to see to it.", "That element is gone; read the page
again (tico browser read app.example.com)." Exit codes are the ones below.

## Exit codes

| Code | Meaning | Retry? |
|---|---|---|
| 0 | Done | |
| 1 | Tico could not be reached, or answered 5xx or 429 after the CLI's own retries; or an unexpected error | Yes, with the same operation ID |
| 2 | Refused: the server said no (403, 409, 422, a task refusal with what is missing and what to run next) | No, not as is |
| 3 | `tico task worktree add` at the bot's worktree limit: Tico holds the task's place and wakes it | End the turn |
| 4 | Needs sign-in: no credential (`HUB_API_URL` unset and no profile), a profile that is missing or unreadable, or a token that is invalid, expired or revoked (401, or a sign-in page) | After fixing the credential |
| 5 | Not found (404): no such thing, or not one this credential may see | No |
| 6 | `tico task wait`: the task ended (done or closed) without reaching what it waits for | No |
| 64 | Usage: an unknown command or flag, a missing argument, a task command missing what it needs (`Add --version N`) | No: fix the line |
| 124 | A waiter's `--timeout` passed before what it waits for happened | Wait again if it should |

Only 1 means "the same call may work if retried". The answer to an error is JSON on stdout,
`{"error": <code>, "detail": "...", "retryable": false, "operation_id": "..."}` (`tico browser` and `tico login`
print one sentence instead, unless `--json`); argparse's usage text goes to stderr. Two old exceptions stay: `tico file approved` exits 1 when the file is not approved (`"approved": false` in
its answer), and a command that is gone exits 1 with one line on stderr naming what to use instead.

Before 0.6, 4 and 5 were 2, and 64 was 1.
