# Give a bot a credential (Jira, GitHub, a mailbox, any tool)

Triggered when a bot you build or repair needs a key, token or password, or a human asks you to
connect a tool. Budget 5 minutes. The human never leaves the chat, and the value never goes through you
unless they pasted it themselves.

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

For route 1 declare the server when you add the tool (the same request as any tool, plus three flags):

    hub tool add <bot> <service> --can read --env <VARIABLE> --mcp-url https://<vendor address> --transport http \
        --header 'Authorization: Bearer ${VARIABLE}'

`${VARIABLE}` must be the `--env` name and nothing else; write it literally, never the value (the server refuses a value, and
`hub tool update <tool-id> --bot <bot> --mcp-url ...` changes it later, as the requester). Tico passes the server to Claude Code, Codex,
Gemini CLI and Grok Build with the credential filled in at run time; Cursor, Antigravity and pi cannot take it, so if the bot runs
on one, say so and offer to move it or use route 2. Then do steps 1 to 6: the credential is stored and granted exactly as below.

## 1. Open the card

    hub credential request <VARIABLE> --for-bot <bot> --label "<your Jira credential>" \
        --format "<the exact shape>" --help-url <https page where they make one>

- `VARIABLE` is the name the bot's `bot.yaml` `tools:` entry declares in `env:`.
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
(`hub credential request` with the same variable replaces the value).

## 3. If they paste the credential in chat

Store it and go on: `printf '%s' "$VALUE" | hub credential set <VARIABLE> --for-bot <bot>`. That keeps it in
Credentials for that bot only and removes it from the conversation. Say in one line: it is saved and
removed from the chat, and next time the card keeps it off the model entirely. Never repeat the value,
write it in a file, a task or a commit, or copy it to another bot.

## 4. Declare it, then check it

The bot's `bot.yaml` `tools:` entry names the variable (`hub tool add <bot> <service> --can read --env <VARIABLE>`). A stored credential
reaches a run only when it is granted to that bot; saving it from a card or `hub credential set` grants it to that one bot.
Then verify with one read-only call the bot itself makes (list one project, read one issue). For an MCP server the bot's
Tools tab (`hub tool list --bot <bot>`) also says whether Tico's runner reached it: reachable, auth failed (the credential
was refused: open the card again) or unreachable (the address is wrong or the vendor is down). Never paste a secret into
`bot.yaml`, a skill, a task or a commit.

## 5. Close what you filed

A task you filed for the human about this credential ("Add the Jira key", "Create the Jira record") is settled
once the test in step 2 passes, or once you did the work yourself: `hub task close <id> --note "Saved and
connected."`. Change what a bot's tool `can` do or its scope with `hub tool update <tool-id> --bot <bot> ...`,
never by removing and adding it.

## 6. What stops you

- The server says only a credential admin can store it: say who (the message names them). They can fill
  the same card.
- The credential exists already, in Credentials or in another bot's file: do not ask again; `playbooks/share-a-credential.md`.
- The bot does not need a credential at all: do not open a card.
