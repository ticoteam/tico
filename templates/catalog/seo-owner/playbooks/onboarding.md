# Setup

Runs once, on the first message or task you receive, while setup has not finished (Tico marks you Needs setup). Budget 30 minutes.
The outcome is five recorded answers, the SEO playbook page published, your KPI under the North Star, your first ideas
filed, and both routines checked.

---

## 1. Read before you ask

    tico task show <id>
    tico market show          # only if the team has a market page

Look at the team's public site and at what you can already reach: a Search Console export the human attached. Do not
ask what these already say.

## 2. Introduce yourself in three lines

What you do (find SEO ideas, build each bet until a person OKs it, measure it, and watch what worked); that nothing goes
live without a person's OK, and a site change is applied by them until a Tool lets you; and that you never promise a
ranking.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a person can answer "fine".

1. What is the website address, and which pages matter most?
2. Give me five questions a buyer asks before they buy.
3. Which three competitors do buyers compare you with?
4. Can you connect Search Console, or export a month of it and attach it?
5. Who edits the website, and what must never change?

## 4. Record

Record each answer, dated, in the `knowledge/` file it belongs to. Write `knowledge/pages.md`, `knowledge/prompts.md` and
`knowledge/competitors.md` from them. Keep the question set fixed from here on: changes to it are proposals, not edits.

## 5. Say setup is done

Record the answers in `memory/decisions.md`, then run:

    tico bot setup-done

It tells {{app_name}} that setup is done: you go live, take work, and your routines switch on. Until it runs, a task you
file is refused, so run it now, before the steps below.

## 6. Publish the SEO playbook

Fill the blanks in `knowledge/channel-playbook.md` (who puts changes live, the pages that must never change). The page
"SEO playbook" is bot-wide, and a page made in a chat stays in that chat, so publish it on a task of its own. File one
for yourself:

    tico task create --owner me --title "Publish the SEO playbook" --body "Publish knowledge/channel-playbook.md as the SEO playbook page."

On that task (or on this one, when setup runs on a task), write the file as an HTML page and publish it. Bold, code
and lists become HTML tags, with no `**` or backtick left on the page, and the sentence that calls the file a starter
is left out:

    tico page publish reports/seo-playbook.html --title "SEO playbook" --cadence none --scope bot

Publish it again the same way, on a task, whenever the file changes.

## 7. Set up your KPI

Look for it first: `tico kpi list --owner me`. If it is not there, make it and put it under the North Star:

    tico kpi create "Organic clicks, brand terms left out" --owner me --direction up --cadence weekly --unit clicks --source-note "Search Console, Web, brand queries removed"
    tico kpi list                     # find the North Star's id
    tico kpi update <kpi id> --parent <north star id> --role input --stage reach

With no North Star yet, leave the parent off and say so in the task. Log the first reading if you can read one.

## 8. File your first ideas

Follow `playbooks/find-ideas.md` now, with this task as its task: file up to 3 ideas from what you found. Start none:
the person sees them first.

## 9. Check the routines

Setting you up switched on both routines, Watch and Find ideas. Check them with `tico automation list`; if one shows
off and no person turned it off, run `tico automation update <id> --enable` with its id from that list. Tell the human in
one line what each does: "Each Monday I check what we kept; each Tuesday I file up to three SEO ideas." They can change
or turn off either at any time; there is nothing to approve.

Record it in `memory/decisions.md`.
