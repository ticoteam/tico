# Run and measure

Your task is in Growth Experiments' Running stage. Two parts: put it live when the task enters Running, then measure it
when it wakes at its end date.

Outcome: the result tagged (`result:failed`, `result:worked-once` or `result:watching`), its Results attached, and on
`result:watching` a Monitoring task filed as its follow-up. Jev checks the page, and the task is Done.

The same text is shipped to every channel Owner. Your tag and who applies changes are in
`knowledge/channel-playbook.md`.

---

## Part 1: put it live

    tico task show <id>

Put live exactly what the person OK'd on the Release report, nothing more. That one approval covers the bounded test: its
total spend, audience, allowed edits and end date. Where a granted Tool lets you, make the change and record what you
changed and when. Where none does, file the exact change for the person your playbook names:

    tico task create --owner <your manager> --parent <id> --title "Apply: <the change>" --message-file <message file>

The first owner is your Manager, the bot your line in `tico team show` reports to: it checks the task once and
passes it to the person. A task filed straight to the person is refused.

Write the message to a file first. Its first paragraph is what the person reads first: one or two plain sentences, under 40
words, saying what to change and where, with no links, ids or file paths. Below it, after a blank line: exactly what to
change, where, and the Release report link. If the command is refused, nothing was filed: fix what the refusal says and
run it again, in this run. Say you filed it only once the command printed the new task's id.

The task waits until they say it is done, and that wakes you. Do not snooze it while it waits.

Once it is live, check it is live as OK'd (the page loads, the ad runs, the email has gone out). Then add the `idea:live` label and the live URL to the task note, and snooze the task to the end date the Release
report names. It wakes you then. Do not snooze it before it is live. Use the version you read for each command, and
read the task again after each one:

    tico task label <id> --add idea:live --version N
    tico task update <id> --note "Live since <date> at <live URL>" --version M
    tico task snooze <id> --until <end date> -m "Measure on <end date>" --version P

If the stop-early number or event happens before then, stop it the same way it went live, and measure now, from the
day it went live to the day you stopped it.

## Part 2: measure

Read the number the Release report named for the run's period, from the day it went live to the day it stopped (its end date, or earlier if you stopped it), and for the
same length of time before it, each from a dated source. Then decide:

- **Worked** means the number beat the baseline and reached at least 60% of the predicted change.
- `result:watching`: it worked and stays on, so it needs watching.
- `result:worked-once`: it worked and was a one-off, so there is nothing to watch.
- `result:failed`: it did not work. Take it down if it is still live, the same way it went up.

Tag it:

    tico task label <id> --add result:<failed|worked-once|watching> --version N

On `result:watching`, first file the Monitoring task. It says what the kept thing is (a link), its KPI and the range it
should stay in, and this experiment:

    tico task create --owner me --pipeline growth-monitoring --label <your channel tag> --title "<the kept thing>" --message "<link>. KPI: <number>, range <low> to <high>. From <this task>."
    tico task relate <monitoring id> <id> --kind follow_up

It lands in Watching, where your Watch routine checks it.

## The Results

- **Before and after**: the number for each period, before and after the day it went live, with its dates and its source.
- **The prediction** from the Release report, and how much of it was reached.
- **The result** and its tag, in one line, with why that tag: on `result:watching` what stays on and is watched, on
  `result:worked-once` why there is nothing to watch.
- **What we learned**, in one or two lines.
- On `result:watching`, **the Monitoring task**, named (its id and title) and linked.

    tico page publish reports/<task>-results.html --title "Results: <title>" --task <id>
    tico task attach <id> page:<file id> --req "Results" --version N

Colour the page as `.claude/skills/build-the-page` says, so it reads in dark mode.

Jev checks it and the task is Done.
