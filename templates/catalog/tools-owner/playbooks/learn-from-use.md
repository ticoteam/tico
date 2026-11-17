# Learn from how tools are used

Schedule: daily at 08:30 team time (routine `learn-from-use`). Budget 10 minutes: a cheap read, not a review. Read
`skills/manage-tools-and-keys/SKILL.md` and `skills/learn-tools/SKILL.md` first.

Inputs: `knowledge/company/usage.md` (your own notes: read it first), `tico tool index`, and for each API tool worth a
look `tico tool activity <id> --since 24h` (every caller's calls through the proxy and key uses, a summary of repeated
calls and errors, who added its keys; never a value). Your open tasks: `tico task list --owner me`.

Outcome: `knowledge/company/usage.md` says who uses which tool and how, the vendor errors that keep coming back with
their fix, and the tools nobody uses; each new problem has one action; nothing already noted or acted on is raised again.

Done when: usage.md is updated and committed, each new finding below has its one action, and the run is finished.

1. Read `knowledge/company/usage.md`. Each finding there carries the date you acted and how (`told ops on task 1234`,
   `card on task 1250`, `asked ana`). A finding already there is not new: act again only when it is worse (a different
   status, a new bot, twice the calls) or the task you opened ended without fixing it.
2. Read `tico tool index`. Pick the API tools worth a look, at most 20, problems first: a status other than Working,
   Works (limited) or Not checked; then tools with people or bots on them. Skip the rest.
3. For each, `tico tool activity <id> --since 24h --limit 100`. No rows: note the date of the last use you know of.
   For a tool with refusals, read them alone with `--result refused`. Read the `summary`, not every row.
4. Note in usage.md, one short section per tool: who calls it (bots, people), its typical calls (method and endpoint,
   ids folded to `:id`), errors seen with count and date, and the fix once you know it. Read the vendor's guide
   (`knowledge/vendors/<vendor>.md`, then `knowledge/vendors/learned/<vendor>.md` and `knowledge/company/<vendor>.md`)
   to explain an error: what a 401, 403 or 404 means at that vendor.
5. Act on each new finding, err on action, one action each:
   - **The vendor rejects the key** (401s, status Key rejected): one task for yourself (`tico task create --owner me
     --title "<tool>: key rejected"`), then a card for the bot whose key it is on that task (`tico credential
     request`), as the manage skill says.
   - **Tico refused a bot's calls** (`refused`: no level, Read on a write, no stored key): the bot's job needs it, so
     ask the person who can give it on a task of yours (`tico task ask`), naming the bot, the tool and the level.
   - **One bot keeps getting the same vendor 4xx on one endpoint** (3 or more in a day, no 2xx on it since): tell
     that bot how to call it right. Find the cause in the vendor's guide or the vendor's docs, then file it to the bot
     (`tico task create --owner <bot> --title "<tool>: <method> <endpoint> answers <status>" --message "<the cause and
     the right call>"`). A 403 that is a missing scope on the key is a key problem, not the bot's: ask the person who
     added the key (the `added_by` of the activity) for a key with that scope.
   - **Nobody used a tool for 30 days** (no rows and no level holder's use you know of): propose removing it, or its
     unused grants, in one question to the person who added it (`tico task ask`). Never delete it yourself.
   - **A vendor you have no guide for, or a guide the errors prove wrong**: one question, as `skills/learn-tools`
     says.
6. Record what you did beside each finding in usage.md (date, task number, who). Commit the repository.
7. Finish: `tico task done <id> -m "<n tools read, n new findings, n acted on>" --version N`. A quiet day says so in
   one line and files nothing.

Never paste a key, a token or a call's body into usage.md or a task: activity has none, and nothing you write may.
