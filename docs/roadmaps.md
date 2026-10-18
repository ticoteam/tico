# Public roadmaps

Roadmaps use tasks, types, labels, comments and parent relations. The Product roadmap type runs
Suggested → Exploring → Spec → Approved → Building → Shipped (or Declined); Engineering (or an existing Dev tickets
type, as in [The software factory](software-factory.md)) uses the development flow. Tico knows these types by their
role (`role` in `hub task types`: `product-roadmap` and `engineering`), not by the bot that manages them, so archiving
that bot or handing the type to another keeps the roadmap and its Approved → Building → Shipped sync working. A type
keeps its role when renamed. A type named Dev tickets gets the engineering role when it is created or renamed to that,
even beside Engineering; both then count as a roadmap item's development work. An install from before roles gives them
once on upgrade, to the types the Product Manager and Head of Engineering managed and to Engineering; no task changes.
Every type starts private. A task is public only once someone publishes it with a
public title written for it: the internal title and body are never copied into the public text. A type set to
default public publishes a new task only when its public title is given at creation; without one the task starts
private. A bot (or the Assistant or BotOps acting for a person) cannot create a task with public text or as public;
it is refused, and the bot creates the task and then asks for an approved publication. A type default applies only when a task is created; existing tasks and tasks moved to another type keep
their visibility. Older tasks start private.

In Settings → Tasks, create a label such as `roadmap`. In Public roadmap, select that label, add
an exact website origin such as `https://acme.example`, and save. Empty labels disables roadmaps.
**Suggestions go to** picks the bot that receives visitor suggestions: **Automatic** (the field says which bot
that means now, or that no bot can take them) or any active bot. A chosen bot that has since been archived or paused
shows as such, with the bot suggestions go to instead. Owners and admins change these settings; everyone else sees
them read-only.
A public task carrying that label appears through the API. Several labels can be enabled and a
task can appear on several roadmaps. Publication is separate from the existing two-party Private
setting: a two-party private task can never appear publicly. Child tasks are excluded; only their
completion counts are shown.

Tico provides the API and an internal ranking view, not a hosted public roadmap page. Each
installation's host scopes requests to its company, as it does for the internal API; use a different
installation host for another company. No public endpoint enumerates labels.

## Calls from your website

GET and HEAD work without an Origin, including server-side rendering. Only allowlisted origins
receive Access-Control-Allow-Origin; responses always vary by Origin. Writes require `Origin`
from the roadmap allowlist. Use no credentials. HTTPS origins match exactly; HTTP is allowed only
for localhost. Preflights accept GET, HEAD, POST, DELETE and Content-Type. Origin is browser-only
friction, not visitor identity: non-browser callers can supply it themselves. Writes proxied through
your company server share that server’s daily IP allowance.

```sh
curl https://tico.acme.example/public/roadmaps/roadmap \
  -H 'Origin: https://acme.example'

curl https://tico.acme.example/public/roadmaps/roadmap/tasks/TASK_ID/requesters \
  -H 'Origin: https://acme.example' -H 'Content-Type: application/json' \
  -d '{"email":"customer@acme.example","organization":"Acme","note":"We need larger exports"}'
```

The add response returns `requester_token` once. Save it privately for that visitor and remove their
request with `DELETE /public/roadmaps/roadmap/tasks/TASK_ID/requesters/TOKEN`. Tokens are random,
stored only as hashes, bound to one task, and unusable after removal. A token removes its request whatever became
of the task: private, no longer labelled, on a roadmap since disabled, or deleted (the requester also leaves the
deleted task's trash copy, so a restore does not bring them back). An unknown token gets the same 404 as an
unknown task. Contact details are unverified and only for manual follow-up.

| Method | Path after `/public/roadmaps/{label}` | Body / result |
| --- | --- | --- |
| GET | (empty) | `tasks`, `next_offset` |
| GET | `/tasks/{id}` | `task`, `comments`, `next_offset` for comments |
| POST | `/suggest` | `title`, `summary`, optional requester details; returns private task `id` and `requester_token` |
| POST | `/tasks/{id}/requesters` | Optional `name`, `email`, `phone`, `other`, `organization`, `note`; returns token and count |
| DELETE | `/tasks/{id}/requesters/{token}` | Removes that requester |
| GET | `/tasks/{id}/comments` | `comments`, `next_offset` |
| POST | `/tasks/{id}/comments` | `text`; returns the public comment |

Lists accept `limit` (up to 100) and `offset`; follow `next_offset` until null. Pages are capped at
256 KB. A task contains only `id`, `title` (public title), `summary` (public summary), public step
name (`public_name` on its step, initially copied from the internal name), requester count and `parts: {done, total}`. Public comments contain only id, text, creation
time and `author: Team|Visitor`. Nothing returns requester names, contacts, internal discussions,
attachments, assignees, bot work, labels or child identities. Render public text as text or sanitized
Markdown. Comments, suggestion titles and suggestion summaries allow at most two links and no embedded
link/script/iframe HTML.

Suggestions create private Product roadmap tasks at Suggested, owned by the bot that receives suggestions,
with the label and submitter's request. They queue quietly for that bot's next run and need team
review before publication. The receiving bot is `suggestions_bot` in settings (an active bot's slug; owners and
admins set it with `hub roadmap settings --suggestions-bot SLUG`), or **Automatic** when unset (`--automatic`, or
`null`): the first active or planned bot made from the Product Manager template, else an active bot made from the Head
of Product (`product-lead`) template. While the chosen bot is not active (archived, paused, quarantined or planned)
suggestions go to the Automatic bot; they never go to an archived bot. Reading the settings returns
`suggestions_receiver`, the bot suggestions go to right now (`bot`, display `name`, and `source`: `chosen`,
`automatic`, `chosen_archived`, or `chosen_inactive` for a chosen bot that is paused, quarantined or planned). With no
receiving bot a visitor gets 409 "Suggestions aren't being accepted right now.", ahead of either limit below; nothing
is stored and their daily allowance is not used. At most 20 visitor suggestions wait at Suggested at once across every visitor
(`suggestion_limit` in settings, 1 to 1000); while that many wait, a new suggestion gets 429 until the team moves
some on, and the first refusal of each UTC day sends the receiving bot a notice saying so. Successful suggest, add, remove and comment actions share one limit
per IP per UTC day across every roadmap (default 10, configurable in settings); exhaustion returns
429. Failed writes roll back their charge. The server uses the connection IP, with forwarded IPs
accepted only through the deployment’s trusted proxy configuration (below). IPv6 visitors share
a /64 bucket; an IPv4-mapped IPv6 address (`::ffff:a.b.c.d`) counts as its IPv4 address.

**Idea triage** (off unless the server sets `TICO_DECISIONS_IDEA_TRIAGE=1`; `questions/idea-triage.json`,
`backend/idea_triage.py`). Before a suggestion the form will accept is recorded, Decide reads its title and summary:
is it a bug or small change, or a feature, and is it the same matter as one of the open, non-private Product roadmap
items (up to 100, most recently updated first). A bug or small change at 80% or more goes to the active Head of
Engineering (`engineering-lead` template) instead of the receiving bot. When an open item matches at 70% or more, the
new task opens with a line naming it and is related to it. Routing only: a feature always goes to the receiving bot,
which still triages it, and the visitor's reply is the same either way. Decide off or unconfigured, the monthly spend
cap, the daily call budget, an error, no answer within 12 seconds, an answer under its threshold, or no active Head of
Engineering leave the suggestion with the receiving bot exactly as without triage. A suggestion the form refuses
(limit, full queue, no receiver) makes no call. Each call is a `judge.call` audit row labelled `idea-triage@1` with
the answers, route and model, never the visitor's text.

While at least one public label is enabled, **Settings > Health** tells owners and admins when suggestions don't reach
the bot they expect, from the same rule that routes them:
- **No bot receives roadmap suggestions**: visitors are being turned away. Choose an active bot, or make a Product
  Manager or Head of Product bot active for Automatic.
- **The bot chosen for roadmap suggestions is archived; suggestions now go to *name***, or **isn't active** when it is
  paused, quarantined or planned. Make the chosen bot active again, or choose another bot or Automatic.

Each links to Settings > Tasks > Public roadmap and clears by itself: the first once any bot receives suggestions,
the second once the chosen bot is active again or another choice is saved. With no
enabled label neither shows, and when the fallback finds no bot either, only the first one shows.

## Internal work

Tasks show Requested by, with external and teammate requesters separately. Contacts, organization,
quotes, source and Important stay internal. Teammate status comes from a verified actor, never from
an email claim. The roadmap ranking view sorts by total requests, external requests, important
requests, organization (alphabetically first nonempty organization), or numeric size.

Use `/api/v2/roadmaps/settings`, `/api/v2/roadmap-tasks`, and `/api/v2/tasks/{id}/requesters`
(GET/POST), `/requesters/{requester_id}` (POST partial update), and `/requesters/{requester_id}/delete`
(POST). Internal writes require the usual Idempotency-Key. Requester lists are paged and do not
return tokens; an internal add shows its token once and an idempotent retry returns only its id. Task updates accept `visibility`, `public_title`, `public_summary`, `size`, and
`approval_id` (`size: null` clears an estimate); type updates accept `default_visibility`. A type's manager is fixed
by its role: a type update naming `manager` gets 422, which points to `suggestions_bot`. Public title and summary
start empty and are only what someone writes for them; task creation accepts `public_title` and `public_summary`
beside `visibility`. Publishing (`visibility: public`, at creation or later) needs a public title, and is refused
with a clear error without one.

A bot proposes exact public changes through `POST /api/v2/tasks/{id}/publication-preview` with
`public_title`, `public_summary`, `visibility`, `comment`, or a separate `labels` list. Request a `publish` approval with its
returned `payload` and task id, wait for the human decision, then pass `approval_id` to the update
or public comment (`public: true`). Bot-created tasks start private while approval is enabled, including Assistant/BotOps acting for
a person. Publishing, public edits/replies, and attaching an enabled roadmap label to an already
public task need a new approval each time. Owners/admins can relax this in Public roadmap settings. Moderation uses
`POST /api/v2/tasks/{id}/comments/{comment_id}/moderate` with `action: hide|show|delete`.

These routes also have hub MCP tools and CLI commands of the same names: `hub task requesters`, `hub task
requester-save`, `hub task requester-remove`, `hub task publication-preview`, `hub task comment-moderate`, `hub
roadmap tasks` and `hub roadmap settings` (flags change only the settings named). `hub api` reaches every route.
`hub task create` takes `--public-title`/`--public-summary` beside `--visibility`, and `hub task update` supports
publication fields, size and approval id. `waits_on` / `waiting_on` still means blocked on someone;
requesters are independent. Nothing sends automatically; follow-ups require a person's request
and the existing outbound send switch.

The PM gathers requests and comments, researches and drafts a spec, and proposes graduation as an
existing Decide card. After approval, it moves the roadmap task to Approved and files Engineering
children through Head of Engineering. The server moves Approved/Building/Shipped between Building
and Shipped as engineering children open, finish or reopen. Done, closed and declined children count
as finished, and Shipped requires at least one child that shipped: one at a done step, one closed straight from
it (accepting finished work closes it), or a closed one whose type has no done step (the default Engineering
type's Done; there every close counts, since that type has no other closed step). A Dev ticket closed from any
other step, such as a duplicate or one declined first, did not ship. When every child finished without shipping,
the item returns to Approved. With no Engineering children, manual steps remain unchanged. Requesters remain on the roadmap task.


## Client addresses behind a proxy

`TICO_TRUSTED_PROXIES` lists the IP addresses or CIDRs whose forwarding headers are believed, including
`127.0.0.1/32,::1/128` for the supervised Rust relay. A native install's default trusts only loopback; remote
clients cannot choose their IP with forwarding headers.

The standard Docker Compose file needs no change: Caddy and cloudflared reach the server only over a dedicated
`edge` network (`172.30.254.0/28`) from fixed addresses (`172.30.254.2` and `172.30.254.3`), and the server's
default `TICO_TRUSTED_PROXIES` is loopback plus exactly those two addresses. Other containers on that network draw
addresses from its upper half (`172.30.254.8/29`), so they never take a front door's. If that subnet clashes with a
network on your computer, set `TICO_EDGE_SUBNET`, `TICO_EDGE_DYNAMIC_RANGE`, `TICO_CADDY_ADDRESS` and
`TICO_CLOUDFLARED_ADDRESS` together in `.env`; the trusted list follows the two addresses. Setting
`TICO_TRUSTED_PROXIES` in `.env` replaces the whole list. With your own proxy in front of a native install, list its
addresses, never a range shared with untrusted machines.

`TICO_CLIENT_IP_HEADER=x-forwarded-for` (default) works behind both Caddy and cloudflared: the relay appends its
peer to the chain, and the API chooses the rightmost untrusted address. `TICO_CLIENT_IP_HEADER=cf-connecting-ip` is
for a deployment whose only trusted ingress is cloudflared. Both processes receive `TICO_TRUSTED_PROXIES` through
the standard server command and Docker Compose. Restart after changing these environment settings. Roadmap settings
and requester updates are partial: omitted fields keep their current values, and an empty update is rejected.
