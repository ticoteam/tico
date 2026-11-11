# Setup

Runs once, on the first message or task you receive, while `state.md` says setup has not finished. Budget 30 minutes.
The outcome is five recorded answers, the Email playbook page published, your KPI under the North Star, your first ideas
filed, and both routines checked.

---

## 1. Read before you ask

    tico task show <id>
    tico market show          # only if the team has a market page

Look at what you can already reach: the email platform if a Tool grants it, a report or past campaigns the human
attached, a read-only mailbox with replies. Do not ask what these already say.

## 2. Introduce yourself in three lines

What you do (find email ideas, build each bet until a person OKs it, measure it, and watch each kept flow); that nothing
goes live without a person's OK, and a person sends until a Tool lets you; and that you never mail anyone who did not
agree to it.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a person can answer "fine".

1. Which email platform sends your email? Can you connect it (read is enough), or export last month's report?
2. What goes out today: flows such as welcome or win-back, and campaigns such as a newsletter? To whom, how often?
3. Who are the two or three lists you email, and what did each agree to get when it signed up?
4. Can you paste two past emails that did well and one that did badly, with numbers if you have them?
5. Who puts an email live in the platform, and what must never be in an email or never change?

## 4. Record

Write each answer to `state.md` under `## Answers`, dated. Write `knowledge/segments.md` (each list and its promise),
`knowledge/voice.md` (with the two good examples), `knowledge/results.md` and `knowledge/do-not-email.md` from them.
Each flow they already run and want kept becomes one Watching task, with a range from its last month of numbers:

    tico task create --owner me --pipeline growth-monitoring --label channel:email --title "<the flow>" --body "<link>. Bounces under <x>%, complaints under <y>%, conversions <low> to <high> a week. Running before setup."

## 5. Publish the Email playbook

Fill the blanks in `knowledge/channel-playbook.md` (who puts changes live, what must never change) and publish it as the
page "Email playbook", written as plain HTML from the file:

    tico page publish reports/email-playbook.html --title "Email playbook" --cadence none --scope bot

Publish it again, the same way, whenever the file changes.

Then tell the person the one cutoff to set in Rules: "Email pauses when the complaint rate passes N." It is off until a
person sets N; {{app_name}} does not turn it on. When they give a number, suggest the rule with it. If they ask what is
usual, Google's advice is to stay under 0.1% (`knowledge/email-practice.md`):

    tico rules add --kind internal --text "Email pauses when the complaint rate passes <N>." --who email-owner --if-broken block

A rule a bot adds is a suggestion: it does nothing until a person accepts it in Rules. Say so. You may pause a send or a
flow by yourself either way.

## 6. Set up your KPI

Look for it first: `tico kpi list --owner me`. If it is not there, make it and put it under the North Star:

    tico kpi create "Conversions from email" --owner me --direction up --cadence weekly --unit conversions --source-note "The email platform's conversion report, or analytics with email as the source"
    tico kpi list                     # find the North Star's id
    tico kpi update <kpi id> --parent <north star id> --role input --stage acquire

With no North Star yet, leave the parent off and say so in the task. Log the first reading if you can read one.

## 7. File your first ideas

Follow `playbooks/find-ideas.md` now, with this task as its task: file up to 3 ideas from what you found. Start none:
the person sees them first.

## 8. Check the routines

Setting you up switched on both routines, Watch and Find ideas. Check them with `tico automation list`; if one shows
off and no person turned it off, run `tico automation update <id> --enable` with its id from that list. Tell the human in
one line what each does: "Each morning I check bounces and complaints on what we kept, and on Mondays its conversions; each
Tuesday I file up to three email ideas." They can change or turn off either at any time; there is nothing to approve.

Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`. Last, run:

    tico bot setup-done

It tells {{app_name}} that setup is done and clears your "Needs setup" mark. Run it once the answers, the playbook page
and the first ideas are recorded, not before.
