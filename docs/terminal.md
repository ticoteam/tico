# Work a company from a terminal

For an agent (or a person) driving a Tico company from a shell: file a request, follow it through Product and
Engineering to a merged pull request, and give the OKs that wait on you. Every command is one call to the company's
API, made as you, so the server decides what you may do; a refusal is printed as the server said it.

## Connect

You need the company's address and a personal token (Settings > Computers > API tokens, or
[Connect an external agent](connect-an-agent.md)). Keep the token in a file and point at the file, so it never sits on
a command line or in a log:

```sh
export HUB_API_URL=https://acme.tico.team
export HUB_TOKEN=tico-file:$HOME/.config/tico/acme-token   # read fresh on every call
scripts/tico whoami                                       # from a checkout of the tico repository
```

`tico` is `scripts/tico` in a checkout (or `python3 clients/hubcli.py`). `tico <noun> <verb> --help` says what a command
does, who may run it, and gives an example.

**Output.** JSON on stdout. A refusal is `{"error", "detail", "retryable"}`: exit 1 when a retry may work (or the line
was wrong), 2 when the server refused. Writes send an Idempotency-Key, so a retried write lands once.

**What a token cannot do.** A personal token is you, except that it cannot give an OK on an *OK* requirement (one a
stage keeps for a person's own word: "An OK is the person's own, in Tico"); give that one in Tico. It can approve or
send back a page sign-off, answer a question, and send back an OK.

## Read the company

```sh
tico company show                 # you, every pipeline and stage, every bot and the stages it holds
tico pipeline list                # the pipelines, each with its manager and stages
tico pipeline show product        # by id or name: each stage, who holds it, by hand or not, what it takes to leave it
tico stage show product-spec      # one stage, its pipeline, the stages before and after it
tico pipeline log engineering     # what happened in a pipeline lately
tico task list --pipeline engineering --stage engineering-build
tico task list --waiting-on me    # open tasks whose turn is yours
tico needs-you show               # everything that waits on you, in one list
```

## File a request and watch it to merged

1. **File it.** `tico task request` puts the task in the pipeline's first stage and gives it to whoever holds that
   stage. Note the `id` and `version` it prints.

   ```sh
   tico task request product "Add a negate(x) function" --body "Why, and what done looks like"
   ```

   A stage held by "whoever the task is for" (General's To do) needs `--owner <bot or person>`.

2. **Start it, if the first stage is by hand.** `tico pipeline show product` says `"by_hand": true` for a stage tasks
   leave only when someone moves them (Product's Ideas):

   ```sh
   tico task move <id> next -m "Ready for a spec" --version <version>
   ```

3. **Wait for your OK.** The holder writes the spec; its sign-off waits on a person.

   ```sh
   tico task wait <id> --needs-me --timeout 2h
   ```

   The last line says what waits on you under `yours`. Read the task and its page with `tico task show <id>` (To leave
   lists each requirement, met or not, and the page attached).

4. **Approve it or send it back.** `approve` finds the one requirement waiting for your OK and gives it for the page
   version attached now; with more than one, add `--requirement <id>`.

   ```sh
   tico task approve <id> -m "Clear and small"
   tico task send-back <id> -m "Say what negate(-0) returns"
   ```

   A question on a task is answered with `tico task answer <id> <requirement> "<answer>" --version <version>`.

5. **Wait for the merge.** Product files the work into Engineering; `--tree` follows the tasks a task files. A task
   that reached a stage or went past it meets `--stage`, so this ends once the Engineering task leaves Merge:

   ```sh
   tico task wait <id> --tree --stage engineering-release --timeout 4h
   ```

   Then the release ships it and Product's Validate stage asks for a check in production: wait for it with
   `--needs-me` again, or for the whole request with `tico task wait <id> --status done`.

## `tico task wait`

```sh
tico task wait <id> [--stage S ...] [--status open|done|closed ...] [--turn STATE ...] [--needs-me] [--tree]
                    [--timeout 30m] [--every 15s]
```

It reads the task every `--every` (one light read for all the tasks it watches; a task is read whole only when it
moved) and prints one JSON line per event:

| `event` | When |
|---|---|
| `start` | The task as it stands now |
| `filed` | With `--tree`: a task it filed (up to 20 watched in all) |
| `change` | A watched task's stage, status, version or turn changed |
| `met` | A condition is met; `reason` says which |
| `ended` | The task ended without meeting it |
| `timeout` | `--timeout` passed |
| `error` | A read failed; it keeps reading when `retryable` is true |

Each task line carries `task`, `number`, `title`, `pipeline`, `stage`, `stage_name`, `status`, `version`, `turn` (whose
turn: `person`, `bot`, `paused`, `tasks`, `source`, `jev`, `room`, `by_hand`, `snoozed`), `who`, `line`, `missing` (what
it still takes to leave the stage) and `yours` (what waits on you, with what you may answer).

Conditions: `--stage` (reached or passed, Done counts), `--status`, `--turn`, `--needs-me`; any one met is enough, on
the task or, with `--tree`, any task it filed. With no condition it waits until the task ends.

Exit codes: **0** met, **5** the task ended without it, **124** timeout, **2** refused (an unknown task or stage). A
bot's turn never waits: there it says how a task waits on a person or another task.

## Over MCP

The same commands are tools on the company's MCP server (`/api/v2/mcp`): `tico_company_show`, `tico_pipeline_list`,
`tico_pipeline_show`, `tico_pipeline_log`, `tico_stage_show`, `tico_task_request`, `tico_task_approve` and
`tico_task_send_back`. `tico task wait` is a shell's only; an MCP client reads `tico_task_show_many` every half minute
instead.
