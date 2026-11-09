# Change a bot's model, or move it to another computer

A saved setting is not a running bot. The job is done when the bot can run on its computer, not when Tico saved the
choice.

1. **Change it as the person who asked.** `hub bot model <bot> <model> --effort <effort>`. In a run something else
   started (the daily update, a notice, a retry once the bot is idle), add `--on-behalf-of <task id>`: the open task
   the person asked you for. A move is `hub bot place <bot> --computer <label>`.
2. **Read `readiness` in the answer.**
   - `can_run: false`: the bot's computer cannot run that model. Tico refuses the change and answers with `fix` and
     `link`. Run `hub computer list`: if another computer the person may use can run it, offer the move first, in one
     sentence: "Release Manager can't run Claude on Tico Team box. Reply yes and I'll move it to PNY Mac, which can."
     Only when no computer can, give the sign-in fix with the link, or offer to keep the current model.
   - `can_run: null`: the computer has not said (offline, or too old to report). Say so and check again.
   - `applicable: false`: an external agent (Hermes, OpenClaw, Grok Bot) runs this bot outside Tico. There is no
     computer to check or put it on; report the change and stop.
   - `can_run: true`: the computer has that AI tool signed in.
3. **Check the bot itself after the next heartbeat** (about a minute): run the same `hub bot model` command again. It
   changes nothing and answers with fresh `readiness`. Done means `reported: true`, `bot_ready: true` and no
   `bot_problems`. If a queued job does not start within a few minutes, run `hub health check` and report what it says.
4. **Report what you checked.** "Head of Engineering is on Claude Opus 5.5 (medium) and ready on PNY Mac", never
   "verified" for a setting you only read back. If it is not ready, set the task waiting on the person with the fix.

A busy bot (`busy`: a run in progress) is changed after the run ends: say so, and retry in the follow-up run with
`--on-behalf-of <task id>`.

Inputs: the bot, the model and effort or the computer the person asked for, from the chat message or the task.
