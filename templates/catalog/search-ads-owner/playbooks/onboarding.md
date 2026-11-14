# Setup

Runs once, on the first message or task you receive, while setup has not finished (Tico marks you Needs setup). Budget 30 minutes.
The outcome is five recorded answers, the Search Ads playbook page published, your KPI under the North Star, each
kept campaign filed in Growth Monitoring, your first ideas filed, and both routines checked.

---

## 1. Read before you ask

    tico task show <id>
    tico goal list --all
    tico market segment tree
    tico tool list --bot <slug>

Check which Tools you hold (an ads account, the CRM) and whether an export is attached. Note each segment, and each
goal that names signups, trials or customers: they tell you what a new customer is. Do not ask what these already say.

## 2. Introduce yourself in three lines

What you do (find search ads ideas, build each bet until a person OKs it, measure it, and watch each kept campaign
every day); that nothing goes live without a person's OK, and an account change is applied by them until a Tool lets
you; and that you never raise a budget outside an approved experiment.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a person can answer "fine".

1. Which search ads accounts do you run, about what do you spend a month on each, and can you connect them with read
   access?
2. What counts as a new customer, where is it recorded, and what may one cost? (Default: the last 90 days' average.)
3. Which campaigns running now do you want to keep, and what is a normal week for each? (Default: the last 30 days'
   spend and cost per result, give or take 25%.)
4. What are the weekly spend ceilings, which brand, product and competitor terms are protected, and which claims may ads
   never make?
5. Who changes the ads account today, and what must never change? Should ads pause when cost per new customer passes
   some multiple of its 30-day average, such as 2 times?

## 4. Record

Record each answer, dated, in the `knowledge/` file it belongs to. Write `knowledge/targets.md`: one block per kept campaign
(segment, purpose, primary conversion, target cost per result, weekly ceiling, range, name prefix), then the protected
terms, the competitor rules, the claims never made, and the test count (default 30 primary conversions per variant).
Create `knowledge/structure.md`, `knowledge/negatives.md`, `knowledge/copy.md`, `knowledge/tests.md` and
`knowledge/changes.md` with their headings and nothing else.

Then file each kept campaign as a task in Growth Monitoring. It lands in Watching:

    tico task create --owner me --pipeline growth-monitoring --label channel:search-ads --title "<campaign name>" --body "<link to the campaign>. KPI: cost per result <value>, range <low> to <high>; spend <low> to <high> a week. Kept at setup."

## 5. Say setup is done

Record the answers in `memory/decisions.md`, then run:

    tico bot setup-done

It tells {{app_name}} that setup is done: you go live, take work, and your routines switch on. Until it runs, a task you
file is refused, so run it now, before the steps below.

## 6. Publish the Search Ads playbook

Fill the blanks in `knowledge/channel-playbook.md` (who puts changes live, anything that must never change). The page
"Search Ads playbook" is bot-wide, and a page made in a chat stays in that chat, so publish it on a task of its own.
File one for yourself:

    tico task create --owner me --title "Publish the Search Ads playbook" --body "Publish knowledge/channel-playbook.md as the Search Ads playbook page."

On that task (or on this one, when setup runs on a task), write the file as an HTML page and publish it. Bold, code
and lists become HTML tags, with no `**` or backtick left on the page, and the sentence that calls the file a starter
is left out:

    tico page publish reports/search-ads-playbook.html --title "Search Ads playbook" --cadence none --scope bot

Publish it again the same way, on a task, whenever the file changes.

**The cutoff.** "Ads pause when cost per new customer passes N times its 30-day average" is off until a person sets N
in Rules. If the person gave a number in question 5, suggest it:

    tico rules add --kind internal --text "Search ads pause when cost per new customer passes <N> times its 30-day average." --who <slug>

A bot's rule is a suggestion: a person accepts it in Rules. If they gave no number, tell them in one line that the
cutoff is off and where to set it. Either way, you may always pause by yourself, and never raise a budget outside an
approved experiment.

## 7. Set up your KPI

Look for it first: `tico kpi list --owner me`. If it is not there, make it and put it under the North Star:

    tico kpi create "Cost per new customer from search ads" --owner me --direction down --cadence weekly --unit USD --source-note "Spend from the ads account; new customers from the CRM or signup records"
    tico kpi list                     # find the North Star's id
    tico kpi update <kpi id> --parent <north star id> --role input --stage acquire

Use the team's own currency for `--unit` if it is not dollars. With no North Star yet, leave the parent off and say so
in the task. Log the first reading if you can read one.

## 8. File your first ideas

Follow `playbooks/find-ideas.md` now, with this task as its task: file up to 3 ideas from what you found. Start none:
the person sees them first.

## 9. Check the routines

Setting you up switched on both routines, Watch and Find ideas. Check them with `tico automation list`; if one shows
off and no person turned it off, run `tico automation update <id> --enable` with its id from that list. Tell the human in
one line what each does: "Each morning I check the campaigns we kept, and on Mondays the search terms; each Tuesday I
file up to three search ads ideas." They can change or turn off either at any time; there is nothing to approve.

Record it in `memory/decisions.md`.
