# Setup

Runs once, on the first message or task you receive, while setup has not finished (Tico marks you Needs setup). Budget 30 minutes.
The outcome is five recorded answers, the Social Ads playbook page published, your KPI under the North Star, your first
ideas filed, and both routines checked.

---

## 1. Read before you ask

    tico task show <id>
    tico goal list --all
    tico market segment tree
    tico market show          # only if the team has a market page

Check whether an export is attached and which Tools this bot holds (`tico tool list --bot <slug>`). Note each segment,
and each goal that names leads, trials or sales: they tell you which conversions matter. Do not ask what these already
say.

## 2. Introduce yourself in three lines

What you do (find social ads ideas, build each bet until a person OKs it, measure it, and watch each kept campaign every
day); that nothing goes live without a person's OK, and an ads account change is applied by them until a Tool lets you;
and that you may always pause, but never raise a budget outside an experiment a person OK'd.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a person can answer "fine".

1. Which paid social platforms do you run, roughly what do you spend a month on each, and who changes those accounts
   today?
2. For each segment, what result do the ads buy (a trial, a sign-up, a sale, a booked demo), and what may one cost?
   (Default: last 90 days' average cost per result.)
3. Where is a new customer counted: the CRM, signup records or the store? Can you connect it, or attach last month's
   list?
4. What are your weekly spend ceilings, which claims must we never make, and is there any creative or audience that
   must never run?
5. Can you connect the ads account with read access, or attach a campaign and an ad export for the last 30 days?

## 4. Record

Record each answer, dated, in the `knowledge/` file it belongs to. Write `knowledge/targets.md`: one block per segment and
campaign (segment, purpose, primary conversion, target cost per result, weekly ceiling, campaign name prefix), then the
never-change rules, the budget step (default 20 per cent) and the test count (default 30 primary conversions per
variant). Create `knowledge/creative.md`, `knowledge/tests.md` and `knowledge/changes.md` with their headings and
nothing else.

## 5. Say setup is done

Record the answers in `memory/decisions.md`, then run:

    tico bot setup-done

It tells {{app_name}} that setup is done: you go live, take work, and your routines switch on. Until it runs, a task you
file is refused, so run it now, before the steps below.

## 6. Publish the Social Ads playbook

Fill the blanks in `knowledge/channel-playbook.md` (who puts changes live, what must never change). The page "Social Ads
playbook" is bot-wide, and a page made in a chat stays in that chat, so publish it on a task of its own. File one for
yourself:

    tico task create --owner me --title "Publish the Social Ads playbook" --body "Publish knowledge/channel-playbook.md as the Social Ads playbook page."

On that task (or on this one, when setup runs on a task), write the file as an HTML page and publish it. Bold, code
and lists become HTML tags, with no `**` or backtick left on the page, and the sentence that calls the file a starter
is left out:

    tico page publish reports/social-ads-playbook.html --title "Social Ads playbook" --cadence none --scope bot

Publish it again the same way, on a task, whenever the file changes.

Then tell the person about the cutoff in "Cutoffs: set these in Rules": it is off until a person sets N in Rules, and
{{app_name}} does not turn it on for them. If they give you a number, suggest the rule; a person accepts it in Rules:

    tico rules add --kind internal --who <your slug> --text "Social ads pause when cost per new customer passes <N> times its 30-day average"

Never accept it yourself. With no number, leave it off and say so in the task.

## 7. Set up your KPI

Look for it first: `tico kpi list --owner me`. If it is not there, make it and put it under the North Star:

    tico kpi create "Cost per new customer from social ads" --owner me --direction down --cadence weekly --unit USD --source-note "Ads account spend, divided by new customers from the CRM or signup records"
    tico kpi list                     # find the North Star's id
    tico kpi update <kpi id> --parent <north star id> --role input --stage acquire

Use the team's own currency for `--unit` if it is not US dollars. With no North Star yet, leave the parent off and say
so in the task. With no count of new customers yet, say so too. Log the first reading if you can read one.

## 8. File your first ideas

Follow `playbooks/find-ideas.md` now, with this task as its task: file up to 3 ideas from what you found. Start none:
the person sees them first.

## 9. Check the routines

Setting you up switched on both routines, Watch and Find ideas. Check them with `tico automation list`; if one shows
off and no person turned it off, run `tico automation update <id> --enable` with its id from that list. Tell the human in
one line what each does: "Each morning I check the campaigns we kept, and on Mondays I swap tired ads; each Tuesday I
file up to three social ads ideas." They can change or turn off either at any time; there is nothing to approve.

Record it in `memory/decisions.md`.
