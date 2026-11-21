# Give a bot a credential (Jira, GitHub, a mailbox, any tool)

Triggered when a bot you build or repair needs a key, token or password, or a human asks you to
connect a tool. Budget 5 minutes. The human never leaves the chat, and the value never goes through you
unless they pasted it themselves.

Inputs: the bot that needs the tool, and the tool's name, from the human's chat request or the task that asks for it.

Done when: the bot has its credential, or the card is open and the human has been told in one line what remains; the connection test's one-line result is on the task; no value appears in any note.

## 0. Choose the route: the vendor's MCP server first

Bots own their tools and skills, and we prefer the vendor's own MCP server to anything we build. **Tico does not currently manage OAuth renewal
for bots.** Prefer a vendor-supported API credential or a connection with supported automatic refresh. Check the provider's
documentation: some require renewed human consent. In order:

1. **The vendor's official MCP server, if it accepts an API token or key in a header.** Find it: search "<vendor> MCP server", open
   the vendor's own docs (not a blog or a third-party wrapper) and read the address, the transport (`http` = streamable HTTP, or
   `sse`) and how it signs in. `docs/connect-tools.md` has Jira and Confluence (basic auth, `email:token`), Linear (API key),
   PostHog (personal API key) and Sentry (auth token) already checked; say when you could not confirm a fact.
2. **Otherwise a small REST client with an API token**, in the bot's own repository (`skills/<service>/SKILL.md`, or a script under
   `software/`) that calls the vendor's REST API with the token from `env`. This is the route when the MCP server is OAuth only;
   say why in one line. Keep it read-only first. The skill reads the variable; it never prints it or writes it to a file.

For route 1 the server is a tool in Tools: Tools → Add → MCP server (its address, then a pasted token or its sign-in), and the
bot gets a level on it (`tico tool level <tool> <bot> read`). That grant is all the bot needs: Tico passes the server to Claude
Code, Codex, Gemini CLI and Grok Build in its next run, with no `bot.yaml` entry. Cursor, Antigravity and pi cannot take it, so
if the bot runs on one, say so and offer to move it or use route 2. Only a server Tools cannot hold (an older `sse` one) is
declared in bot.yaml instead (`tico tool add <bot> <service> --can read --env <VARIABLE> --mcp-url <address> --transport sse
--header 'Authorization: Bearer ${VARIABLE}'`, with `${VARIABLE}` written literally, never the value), and its key is stored and
granted exactly as below.

## 1. Open the card

    tico credential request <VARIABLE> --for-bot <bot> --label "<your Jira credential>" \
        --format "<the exact shape>" --help-url <https page where they make one>

- `VARIABLE` is the name the bot reads the key under (`LINEAR_API_KEY`).
- `--label` finishes the sentence "<Bot> needs ...": "your Jira credential", "a GitHub token".
- `--format` is the placeholder: `you@example.com:API token` for basic auth, `ghp_...` for a token.
- `--help-url`, when you know it:

  | Tool | Where they make one |
  | --- | --- |
  | Jira, Confluence | https://id.atlassian.com/manage-profile/security/api-tokens |
  | GitHub | https://github.com/settings/personal-access-tokens |
  | OpenAI | https://platform.openai.com/api-keys |
  | Anthropic | https://console.anthropic.com/settings/keys |
  | Slack | https://api.slack.com/apps |

Then say in one line that a field is open in the chat, and keep working on everything else. Do not ask
for the value in words.

## 2. When they save it, you are woken

You get a message from them that says it was saved. Run the bot's own read-only check of the
connection (read one Jira project, list one repository), then report in plain words what it showed
("Connected to Jira: project HTM loaded."). If it fails, say why in one line and open the card again
(`tico credential request` with the same variable replaces the value).

## 3. If they paste the credential in chat

Store it and go on: `printf '%s' "$VALUE" | tico credential set <VARIABLE> --for-bot <bot>`. That keeps it in
Credentials for that bot only and removes it from the conversation. Say in one line: it is saved and
removed from the chat, and next time the card keeps it off the model entirely. Never repeat the value,
write it in a file, a task or a commit, or copy it to another bot.

## 4. Check it

A stored credential reaches a run when it is granted to that bot, and nothing else is needed: no `bot.yaml` entry, and no task to
add one. Saving it from a card or `tico credential set` grants it to that one bot. Then verify with one read-only call the bot itself makes (list one project, read one issue). For an MCP server the bot's
Tools tab (`tico tool list --bot <bot>`) also says whether Tico's runner reached it: reachable, auth failed (the credential
was refused: open the card again) or unreachable (the address is wrong or the vendor is down). Never paste a secret into
`bot.yaml`, a skill, a task or a commit.

## 5. Close what you filed

A task you filed for the human about this credential ("Add the Jira key", "Create the Jira record") is settled
once the test in step 2 passes, or once you did the work yourself: `tico task close <id> -m "Saved and
connected." --version N`. Change what a bot's tool `can` do or its scope with `tico tool update <tool-id> --bot <bot> ...`,
never by removing and adding it.

## 6. What stops you

- The server says only a credential admin can store it: say who (the message names them). They can fill
  the same card.
- The credential exists already, in Credentials or in another bot's file: do not ask again; `playbooks/share-a-credential.md`.
- The bot does not need a credential at all: do not open a card.
