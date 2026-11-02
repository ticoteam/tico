# Company page

The Company page is a read-only site for your company, separate from the app. It shows outside people what you choose
to share: the board and investors get a monthly edition, the public gets a monthly page, and people you invite with a
Tico sign-in can read the private parts. It is **unreleased**; the pieces below ship together.

Nothing reaches the page until a person approves it. The default is **Closed**, and every structured figure starts
**Hidden**. Bots can keep writing updates that feed the page, but they cannot publish an edition, change what is shown,
or add a reader.

## Transparency

One company switch, in the Company page settings:

- **Closed** (default): no page is served to anyone.
- **Private**: only the audiences you have turned on, and only to readers who sign in.
- **Open**: the public page is served too.

Moving to a less open setting takes effect in the same step. The page is rebuilt before the change is reported as done,
and every path that served the old content stops serving it (see [Taking back](#taking-back)).

## Audiences

Three audiences are built in: **Board**, **Investors** and **Public**. You can add more. Each audience has:

- **Sources**: the company update, or the update of any bot or person you pick. A source that is a bot is shown with
  its name, never as the bot.
- **Disclosure**: what each kind of figure looks like to that audience (below).
- **Readers**: for a private audience, the outside people it is open to. The Public audience has none.

An audience gets one edition per period from each source.

## Disclosure

Each figure has a class: revenue, growth, gross margin, burn, cash and runway, headcount, customers, pipeline, product
and roadmap, people, fundraising, legal, security incidents, AI spend, and each KPI and P&L line on its own. For each
audience, a class is one of:

| Mode | What the audience sees |
|---|---|
| **Show** | The value |
| **Rounded** | The value, rounded |
| **Range** | A band that contains the value |
| **Delayed N days** | The value for a period once N days have passed since the period ended |
| **Hidden** | Nothing (the default) |

Presets (**Radical**, **Standard**, **Quiet**) set every cell; you can then edit any cell.

Figures are filtered in code, not by the model that writes the text. Each stat is built field by field from an allowlist
(label, period, as-of date, display value and series), so raw Finance or KPI objects never reach the page. Two checks
run before anything shows:

- **Derivation check.** A shown value must not let a reader recover a hidden, rounded or ranged one. For example,
  total minus the shown parts, a margin next to its revenue, runway next to cash or burn, or a difference between two
  periods. A stat that would do this is held, and the owner is told which stat and why. Nothing shows until it is
  resolved.
- **Delay clock.** A delay counts from the end of the period, never from when the figure was collected or entered. A
  correction to a period that is already public appears on the next rebuild.

## Editions and timelines

An edition is the text for one audience and one period. The server opens the draft when it is due:

| Audience | Cadence | Due |
|---|---|---|
| Board | Monthly | 5th business day of the month after the period |
| Investors | Monthly | 10th business day of the month after the period |
| Public | Monthly | 30 days after the period ends |

Business days are Monday to Friday in the company's time zone; v1 has no holiday calendar. If the server was down when an
edition was due, it catches up once, not once per missed day. **Draft now** opens an edition out of cycle.

Each draft is written in one isolated server call. It has the audience's instructions, its filtered figures and the
source updates you chose for it. It has no memory, no tools and no chat, and no bot runs for it.

Every write of an edition is checked, by anyone who writes it, the Assistant included:

1. A leak check flags any number or proper name that is not in the audience's filtered figures.
2. The judge checks the text against the Outside edition rules.

A failed, flagged or unavailable check **holds** the edition. A person can override a held edition with a note, and the
note is kept with it. Source updates are prose, so the promise is that figures are enforced in code and a person reads
and approves every word.

## Approval

A person approves each edition. The approval covers that exact text, its version, audience, disclosure revision and
figures, and it is the person's own click: the Assistant, bots and "my assistant can decide approvals" cannot approve
for them. An approval that is not used within 14 days lapses; the edition shows that and asks again.

Stats are approved separately. Approving an edition covers its text. Approving the **stats board** covers which figures
appear, in which form, under which disclosure revision. After that, the values refresh daily by code, with no model
involved. Adding a figure or loosening a rule needs a new approval; tightening applies at once.

## Readers and sign-in

A reader is an outside person who signs in with a verified email, added to one or more private audiences. Readers are
not team members: they have no seat, no tasks, no chat, and no bot can message them. Signing in as a reader never adds
them to the team, even when their email domain is allowed to join.

- **Adding readers:** one at a time, or from a CSV. An invite link is single-use, expires, can be revoked, and opens one
  audience.
- **Sign-in:** through the Tico account sign-in. A self-hosted company registers a second callback for the reader
  origin (see [Install advanced](install-advanced.md#company-page-on-its-own-domain)). With no sign-in provider
  configured, private audiences are off, and Settings says why.
- **Removing a reader** ends their sessions at once.
- **Email:** an edition sends each reader a link-only notice, with unsubscribe. The text is not sent by email. Without an
  install mail path, no email goes out and the page still works.
- **Seen by:** page views only, with no email tracking pixels.
- **Reply** and **I can help** work on the page only. Each message is marked as coming from a reader, is capped per
  reader per day, and goes to the owner's queue. It never wakes a bot until a person passes it on.

## Taking back

- **Retract** removes one edition from every audience it was published to.
- **Open to Private or Closed** removes the public page or the private audiences, in the same step.
- Each change rebuilds the page. Responses carry `max-age=60`, so caches clear within a minute.
- Private pages are never cached. Each request checks the reader and their audiences on the server.
- A feed reader or search engine may keep what it fetched before the change. The page says so; Tico cannot recall
  copies outside it.

## Where it is hosted

- **Hosted on Tico:** `<company>.tico.page`. Staff set the address; the page is served from disk on the host, so a
  crawler never wakes a sleeping company. A sleeping company's stats refresh when it next wakes, and the page shows the
  as-of date.
- **Self-hosted:** a separate HTTPS origin you choose, such as `open.acme.example`, set with `TICO_READER_ORIGIN`.
  Setup is in [Install advanced](install-advanced.md#company-page-on-its-own-domain).

Public pages are plain files, one folder per release. A release is never changed after it is written; the newest one is
switched in, and the old one is deleted.

## Reading it from the CLI and tools

Owners and admins can read the page and its editions from the hub CLI and the MCP tools. These are read-only: nothing
here publishes an edition, changes transparency or adds a reader.

| CLI | Tool | Shows |
|---|---|---|
| `hub company-page` | `hub_company_page` | Transparency, public address and last update |
| `hub audiences list` | `hub_audiences_list` | Audiences, built in and added |
| `hub editions list [--audience ID] [--status S]` | `hub_editions_list` | Editions, newest first |
| `hub editions show ID` | `hub_editions_show` | One edition's text, status, checks and approval |
| `hub readers list` | `hub_readers_list` | Readers (owners and admins only) |

The matching API is under `/api/v2` (`company-page`, `audiences`, `editions`, `readers`).

## What bots can do

Bots keep writing the updates that feed the page. They cannot draft or publish an edition, change transparency,
audiences, disclosure, readers, rules or the page itself. A bot that acts for a person follows that person's rights,
except that the approval click stays with the person.
