# Run and measure

Your task is in Growth Experiments' Running stage. Two parts: put it live when the task enters Running, then measure it
when it wakes at its end date.

Outcome: the result tagged (`result:failed`, `result:worked-once` or `result:watching`), a Results page attached, and on
`result:watching` a Monitoring task filed as its follow-up. Jev checks the page, and the task is Done.

The same text is shipped to every channel Owner. Your tag and who applies changes are in
`knowledge/channel-playbook.md`.

---

## Part 1: put it live

    tico task show <id>

Put live exactly what the person OK'd on the Go-live page, nothing more. Where a granted Tool lets you, make the change
and record what you changed and when. Where none does, file the exact change for the person your playbook
names:

    tico task create --owner <your manager> --parent <id> --title "Apply: <the change>" --body-file <body file>

The first owner is your Manager, the bot your line in `tico team show` reports to: it checks the task once and
passes it to the person. A task filed straight to the person is refused.

Write the body to a file first. Its first paragraph is what the person reads first: one or two plain sentences, under 40
words, saying what to change and where, with no links, ids or file paths. Below it, after a blank line: exactly what to
change, where, and the Go-live page link. If the command is refused, nothing was filed: fix what the refusal says and
run it again, in this run. Say you filed it only once the command printed the new task's id.

The task waits until they say it is done, and that wakes you. Do not snooze it while it waits.

Once it is live, check it is live as OK'd (the page loads, the ad runs, the email is scheduled), then snooze the task
to the end date the Go-live page names. It wakes you then:

    tico task snooze <id> --until <end date> -m "Live since <date>; measure on <end date>" --version N

If the stop-early number or event happens before then, stop it the same way it went live, and measure now.

## Part 2: measure

Read the number the Go-live page named for the run's period and for the same length of time before it, each from a
dated source. Then decide:

- **Worked** means the number beat the baseline and reached at least 60% of the predicted change.
- `result:watching`: it worked and stays on, so it needs watching.
- `result:worked-once`: it worked and was a one-off, so there is nothing to watch.
- `result:failed`: it did not work. Take it down if it is still live, the same way it went up.

Tag it:

    tico task label <id> --add result:<failed|worked-once|watching> --version N

On `result:watching`, first file the Monitoring task. It says what the kept thing is (a link), its KPI and the range it
should stay in, and this experiment:

    tico task create --owner me --pipeline growth-monitoring --label <your channel tag> --title "<the kept thing>" --body "<link>. KPI: <number>, range <low> to <high>. From <this task>."
    tico task relate <monitoring id> <id> --kind follow_up

It lands in Watching, where your Watch routine checks it.

## The Results page

- **Before and after**: the number for each period, with its dates and its source.
- **The prediction** from the Go-live page, and how much of it was reached.
- **The result** and its tag, in one line, with why that tag: on `result:watching` what stays on and is watched, on
  `result:worked-once` why there is nothing to watch.
- **What we learned**, in one or two lines.
- On `result:watching`, **the Monitoring task**, named (its id and title) and linked.

    tico page publish reports/<task>-results.html --title "Results: <title>" --task <id>
    tico task attach <id> page:<file id> --req "Results page" --version N

Jev checks it and the task is Done.
