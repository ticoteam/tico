# Behaviour replay

Re-run real past tasks on a candidate build or prompt, and see which got better and which got worse, before a release.

A **case** is one finished task: what the bot had when it started (title, body, the people's opening messages, the
files on the task, and a hash of the bot's instructions) and what happened (final status, what the bot said, the files
it attached, its runs and minutes). The replay recreates the opening state on a scratch team, hands the task to the
same bot there, and scores the new outcome with the same code as the old one: the BotOps scorers
(`evals/botops/score.py`: jargon, repeated messages, sending the person to a settings page) plus the case's `done`
checks and `limits`.

Each case gets a verdict:

- **regression**: it was done and is not, or was within its limits and is not, or a number got worse and none better;
- **win**: the reverse;
- **mixed**: some numbers better and some worse; **same**: nothing changed.

Numbers, lower is better: person steps, jargon, duplicates, settings detours, runs. Minutes are shown, not judged.

## Cases are private

Real cases hold a customer's words. This repository is public, so they live elsewhere (a private repository or
folder) and are passed with `--cases`. `cases/example.yaml` is made up and shows the format.

1. **Capture** a draft from a finished task:

   ```
   python3 evals/replay/capture.py --url https://tico.example --token <API token> --task <id> [--task <id> …] --out <private folder>
   ```

2. **A person reviews each draft**: cut names, customers, links and figures the replay does not need (what is cut from
   the original transcript is cut from the baseline too), write the `done` checks and `limits`, add `person` answers
   for the questions the bot is likely to ask (answered in order, on the task or on a card it files), and set `reviewed_by` to their name. `prompt.exact: false` means the bot's
   instructions changed after the task ran; the baseline is then the old run on an older prompt.
3. `run.py --check` lists any case that cannot run: unsigned, or still holding a common key or token shape, a password,
   an email address or a phone number. The check is a backstop: a token with no telling shape passes it, so the
   person's read is what keeps a case clean.

`done` checks: `status: [done, review]`, `file_attached: <regex>`, `reply_matches: <regex>`, `reply_not_matches: <regex>`.

## Running it (release preparation)

On a scratch team running the candidate build, with each case's bot set up under the same slug on a computer with a
real model, and running the prompt to compare:

```
python3 evals/replay/run.py --url https://scratch.example --token <owner API token> --cases <private folder> \
    --candidate "v0.3.52 candidate" --report replay.md --json replay.json
```

To compare a **prompt change**, run the cases with the bot on the new prompt; to compare a **build**, keep the prompt and
change the server and runner. The report names the instructions hash each bot actually ran. Each case takes one or more
real model turns, so it is not run per pull request. Model output varies: rerun a single regression with `--only <id>`
before believing it.

It refuses a server that holds the cases' original tasks: the live team's bots would get them. Write `--report` and
`--json` outside this repository too; they hold the cases' text.

Exit code 0 only when every case ran and none regressed.
