# Chat goals and commands

> **Beta**: chat goals work today and may still change. See [Beta features](support.md#beta-features). Steering is no
> longer Beta: a message you send while the bot is answering goes into that turn ([harnesses](harnesses.md)).

Pin a goal in a bot conversation to let its native harness keep working toward an outcome.
Claude Code and Codex support chat goals when the assigned computer reports that capability.
Older computers and other harnesses report `supported: false`. Goals belong to their own
conversation, including a branch; the Assistant is outside this first version. A bot can also
work each of its tasks as a goal (`task_goal: on`, see [Sessions](sessions.md#task-goals)).

Use `/goal <objective>` to set a goal, or `/goal edit <objective>`, `/goal pause`,
`/goal resume` and `/goal clear`. An objective is 1–4,000 characters. Pause retains its text;
resume starts it again. Anyone who can chat in the conversation may change its goal. Shared
room members see the same goal, and personal rooms retain their usual privacy.

The harness reports Working, Paused, Met or Stopped, with its reason. Met and Stopped add a
line to the conversation. They create no Updates item or Slack message. Native continuation
rules, the bot's maximum run duration and Tico's existing spend limits still apply. A running
turn finishes under the existing spend-limit policy; bots over their limit take no new job.

The harness gets the objective plus one standing condition: when the only work left is waiting
on someone or something else, the bot says "Waiting on: <what>" and stops, and the goal shows
Paused with that line. A reply, a task answer, a watcher or a routine wakes the bot; Resume
starts the goal again. A goal the harness keeps sending back to work with no person writing in
between (30 "not met" verdicts from Claude Code's judge, or 100 Codex continuation turns:
`MAX_GOAL_TRIES` and `MAX_CODEX_GOAL_TRIES` in `runner-rs/src/goals.rs`) is paused and cleared in the
harness, and the bot's reply ends with "Goal paused after N tries without progress: <last reason>".
Codex's `blocked` status also shows as Paused.

Typing `/` shows Tico's commands (`goal`, `new`, `task`, `branch`, `help`) and the headless
commands reported by the computer. Claude Code supports `compact`, `clear` and `model`.
Codex uses app-server equivalents for `compact` and `review` when its installed protocol
supports them. Unknown commands remain ordinary messages. Bot skills are not included yet.

The CLI and MCP use the same API and access checks:

```sh
hub chat goal <conversation-id>
hub chat goal <conversation-id> set "Write the Acme summary and verify it"
hub chat goal <conversation-id> pause
hub chat goal <conversation-id> resume
hub chat goal <conversation-id> edit "Write a shorter Acme summary"
hub chat goal <conversation-id> clear
hub chat send ops "/compact" --conversation <conversation-id> --command
```

MCP `hub_chat_goal` takes `conversation_id`, optional `action` (`get`, `set`, `edit`, `pause`,
`resume`, `clear`) and `objective` for set or edit. `hub_chat_send` and `hub_message_send`
accept `command: true` for verbatim harness delivery.

`GET /api/v2/conversations/{id}/goal` returns `{goal, supported, commands}`.
`POST` on that path takes `{action, objective?}` and returns `{goal}`; unsupported harnesses
return HTTP 409 with `goal_unsupported`. Goal fields are `id`, `conversation_id`, `bot`,
`objective`, `status` (`active`, `paused`, `met`, `stopped`, `cleared`), `note`, `set_by`,
`set_at`, `updated_at`, `ended_at`.

The conversation snapshot includes `goal`. Its existing watch stream also sends an SSE
`goal` event whose data is `{type: "goal", goal}`. Bot lists expose `goal_active` only for
active goals in conversations the viewer can read. The message endpoints accept
`command: true`; the runner bypasses its normal prompt wrapper for that message.

Codex uses `thread/goal/set` and `thread/goal/clear`, with native paused/active status when
available. The runner retains the lease through Codex's native continuation turns. Claude
uses `/goal` prompts on its resumable session. Some Claude versions omit evaluator records
from stream-json; the runner reads only new `goal_status` attachments from that exact
session's native transcript, preserving the distinction between met and impossible.
Goal changes supersede queued controls, and late events from old revisions or leases do
not change the current goal.
