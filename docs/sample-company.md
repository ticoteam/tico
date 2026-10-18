# The sample company

A new Tico can start as **Sample Co**, a made-up company of five bots, so a person sees Tico working within minutes
instead of building a team first. Sample Co sells Brightbook, a small booking app for hair salons. Its team:

| Bot | Runs on | Its goal | Its first task |
|---|---|---|---|
| Assistant | Claude | The owner knows the team and what each bot does | Write a one-page welcome to Sample Co |
| Head of Product | Claude | Text-message reminders are specified and agreed | Write a one-page brief for the reminders |
| Builder | ChatGPT | Text-message reminders have a build plan | Plan how to build the reminders (there is no code) |
| Support Agent | ChatGPT | Every customer question has a reply ready within a day | Draft replies to three made-up customer questions |
| Content Marketer | ChatGPT | One useful post for salon owners drafted each week | Draft a short post on cutting no-shows |

BotOps, the Librarian and the Goal Manager are built in as on every Tico, and follow the company's default model.

These are real bots on the person's own subscriptions, not [demo mode](demo.md). Each first task takes a few minutes,
puts its result on the task as one comment, and then asks the owner one Yes/No question, **Approve the welcome note?**
and so on, which is the card in **Needs you**. Yes and the bot marks the task done; No and it changes the work as
asked and asks again. Nothing a first task does is sent, posted or published outside the company: the Support Agent
and the Content Marketer only draft.

The approval is a question (`hub task question`), not a Tico [approval](permissions.md): approvals are for an
outward action, such as a send, spending, publishing or a merge, and a first task does none of them.

## Starting it

Only a company that is not set up yet can start as the sample: the first run is unfinished and no bot is active. A
company that is set up is left alone.

- **From the welcome.** The first screen of the first run (`#/welcome`) offers **Start with a sample company**. One
  click, by the owner, does the whole first run (`POST /api/v2/setup/sample`) and lands on the screen after Create.
- **At install.** `TICO_SAMPLE_COMPANY=on` in the server's environment loads it on the first start whose owner is on
  the roster, once. A restart never loads it again, and a rehearsal never loads it.
  Unset or any other value loads nothing.

## What it sets up

- **Names and answers.** The first run's answers describe Sample Co, as if the owner had typed them. A team name
  already set (`TICO_TEAM_NAME`, which the recommended install sets to Sample Co, or one saved in the welcome) is
  kept, and the first tasks use it; with none, the team is called Sample Co.
- **Providers.** When no AI provider is chosen yet, Claude (the default) and ChatGPT are turned on. A company that
  already chose keeps its choice, and a bot whose provider is off runs on the company's default model instead. The
  bots still need the two subscriptions signed in on the computer ([subscriptions](subscriptions.md)).
- **Bots.** The four role bots are created as **Create my team** creates them ([what Create does](onboarding.md#what-create-does)),
  then marked set up: the sample's answers stand in for the setup chat, so each one's first task is picked up as soon
  as a computer runs it. The computer makes each bot's repository with its `state.md` already saying setup is done
  (`sample.setup` in the first-run record), because a template's own instructions ask the setup questions before any
  other work until `state.md` says otherwise. Their routines stay off until a person turns them on, so a sample uses little of the
  subscriptions.
- **Goals and tasks.** One goal per bot, and its first task under that goal, filed by the owner.

The team is the company file `backend/company_file/sample_company.yaml` (format 0.2), with two fields only the sample
reads: each employee's `runs_on` and `starter_tasks`. The loader is `backend/sample_company.py`.
