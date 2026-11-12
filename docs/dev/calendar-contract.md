# Calendar build contract

The shared contract for the tracks building Calendar. The product spec is
`/Volumes/PNYRP60PSSD/orca/workspaces/emp-tico-open-source/Calendar-Sidebar/reports/2026-11-10-calendar-spec.md`
(v0.2). Read its sections 1 (decisions), 5–13 before you start. Where this file and the spec differ, this file wins.

Integration branch: `chris/calendar` (from main d7b1a50cb, v0.6.0-rc.1). Each track works on its own branch
`chris/calendar-<track>` in its own worktree and never pushes; the lead merges tracks into `chris/calendar`.

## Words

- A **calendar ref** is a string: `human:<id>`, `bot:<id>`, `shared:<calendars.id>`, `team:<groups.id>` (expands to its
  members), `google:<google calendar id>` (a calendar on the viewer's own Google calendar list). `me` = the caller.
- An **occurrence** is one instance on the grid, already expanded. Every read returns occurrences; nothing outside
  `calendar_store` and `gcal_live` knows about rules or Google's shapes.
- **Home**: `google` (a person or mailbox with Google connected, through the company key), `tico` (bots, shared
  calendars, people without Google), `derived` (runs, scheduled automations, due dates; never stored as events).

## Occurrence (JSON, every endpoint)

```
occurrence_id   string, stable per instance:
                  tico:<event id>:<original start, UTC ISO Z>      (single events: tico:<event id>)
                  google:<google calendar id>:<google event id>    (Google instance ids already carry the start)
                  run:<attempt id> | runs:<bot id>:<start ISO>      (a grouped block, see dense runs)
                  auto:<automation id>:<start ISO> | due:task:<id> | due:kpi:<id> | away:<absence id>
event_id        the series or single event id (tico id, google event id or recurringEventId); null for derived
calendar        {ref, type: human|bot|shared|google, id, name}
home            google | tico | derived
kind            event | run | due | away
title           string ("Busy" when busy_only)
start, end      ISO 8601 with offset (UTC "Z" from the server); for all_day: "YYYY-MM-DD" dates, end exclusive
all_day         bool
time_zone       IANA zone of the event, or null
attendees       [{type: human|bot|external, id|null, email|null, name, response: needs_action|accepted|tentative|declined}]
response        the viewer's own reply when the viewer is an attendee, else null
busy_only       bool: the viewer may see only the time (title "Busy", no attendees, no links)
recurring       bool
status          confirmed | tentative | cancelled   (cancelled never returned by reads)
meeting_url     join link or null; Google Meet links get ?authuser=<viewer's Google address>
open_url        Google's htmlLink with authuser for home google, else null
links           {thread, meeting, task, automation, attempt}: ids or null
can             {join: bool, edit: bool}
planned         derived runs only: true for a scheduled automation that has not run yet
count           grouped runs only: how many runs the block holds; `children` lists their occurrence_ids
sequence        tico events: the event's sequence (send it back as expected_sequence on edits)
```

## Tables (one migration per track, `python3 scripts/new_migration.py <name>`, Rails names)

Track **core** owns all of these except `reach_settings.book`:

- `calendars(id TEXT PK, owner_type TEXT CHECK IN ('human','bot','shared'), owner_id TEXT, name TEXT, home TEXT CHECK IN
  ('tico','google'), google_calendar_id TEXT, time_zone TEXT, created_by TEXT, created_at TEXT, updated_at TEXT)`; unique
  `(owner_type, owner_id)` where owner_type <> 'shared'.
- `calendar_events` exactly as spec §7 (id, calendar_id, uid, title, notes, location, meeting_url, all_day, starts_on,
  ends_on, starts_at_local, ends_at_local, time_zone, starts_at, ends_at, rrule, until_at, recurring_event_id,
  original_starts_at, status, visibility, sequence, created_by, task_id, thread_id, created_at, updated_at) + the indexes.
- `calendar_event_attendees(id, calendar_event_id, attendee_type, attendee_id, email, response, created_at, updated_at)`.
- `absences(id, human_id, starts_on, ends_on, note, created_by, created_at, updated_at)`.
- `calendar_accesses(id, calendar_id, grantee_type ('human'|'bot'|'group'), grantee_id, level ('read'|'read_write'),
  created_by, created_at, updated_at)`, unique (calendar_id, grantee_type, grantee_id).
- `calendar_event_links(id, google_calendar_id, ical_uid, tico_event_id, original_starts_at, link_type
  ('meeting'|'thread'|'task'|'bot'), link_id, created_at)`; a Tico event link uses tico_event_id, a Google one ical_uid.
- FTS5 `calendar_event_search(title, notes, location, content='calendar_events', content_rowid=rowid)` kept in step by
  calendar_store (or triggers).
- `humans`: add `time_zone TEXT`, `work_starts TEXT` ('08:00'), `work_ends TEXT` ('18:00'), `work_days TEXT`
  ('mon,tue,wed,thu,fri'); null means the default.

Track **reach** owns: `reach_settings.book TEXT` (null = inherit; 'anyone' | 'nearby' | 'none'; default 'anyone').

## Python modules (exact signatures; the routes track imports these)

`backend/calendar_recur.py` (core): pure, no DB.
- `parse_repeat(text: str) -> str`: accepts an RRULE (with or without `RRULE:`) or words ("daily", "weekly",
  "weekly on mon,wed", "every 2 weeks on fri", "monthly on the 2nd tue", "monthly on day 15", "yearly"), optionally
  "until YYYY-MM-DD" / "for N times"; returns a normalized RRULE string. Anything outside the spec §7 rule 9 subset
  raises `Problem("repeat", "<why>", 422)`.
- `expand(event: dict, exceptions: list[dict], start_utc: datetime, end_utc: datetime, cap: int = 750) -> list[dict]`:
  each `{original_start_local, original_starts_at (UTC ISO), starts_at, ends_at}` (UTC ISO; all-day uses dates) after
  applying exceptions (moved / cancelled by original start). Wall time + IANA zone; spring-forward gaps move forward
  by the gap, fall-back ambiguity takes the first (fold=0).

`backend/calendar_store.py` (core): the Tico home. `c` is a sqlite connection (store.read/transaction).
- `calendar_for(c, owner_type, owner_id, *, create=True) -> dict | None` (lazily makes a teammate's Tico calendar;
  name = teammate name; time_zone from humans.time_zone or 'UTC').
- `create_shared(c, name, actor, time_zone='UTC') -> dict`, `get(c, calendar_id) -> dict | None`,
  `shared(c) -> list[dict]`.
- `occurrences(c, calendar_ids: list[str], start_utc, end_utc) -> list[dict]` (occurrence shape, minus `can`,
  `response`, viewer-specific fields; `busy_only` False; private events marked `visibility: 'private'` so the caller
  can hide them).
- `create_event(c, calendar_id, fields: dict, actor) -> dict` (fields: title, notes, location, meeting_url, all_day,
  start, end (ISO local or with offset), time_zone, repeat (words or RRULE), attendees [{type,id,email}], task_id,
  thread_id, visibility). Returns the stored event (as an occurrence of its first instance plus `event_id`).
- `update_event(c, event_id, fields, actor, *, original_starts_at=None, scope='all', expected_sequence=None) -> dict`
  scope `this` | `following` | `all` per spec §7 rule 5. A stale expected_sequence raises Problem 409 `stale`.
- `cancel_event(c, event_id, actor, *, original_starts_at=None, scope='all') -> None` (status cancelled; never delete).
- `set_response(c, event_id, attendee_ref, response, *, original_starts_at=None) -> None`.
- `search(c, calendar_ids, q, start_utc, end_utc, limit=50) -> list[dict]` (FTS over title/notes/location, expanded,
  nearest first).
- `absences(c, human_ids, start_date, end_date) -> list[dict]` (kind away), `add_absence(c, human_id, starts_on,
  ends_on, note, actor) -> dict`.
- `purge_cancelled(c, now) -> int` (cancelled rows older than 90 days; called from the hourly Maintenance job).
- Every write: `audit_log` row + `H.event(c, actor, 'calendar.event.created|changed|cancelled', event_id, {...})`.

`backend/gcal_live.py` (google): the Google home, nothing stored but the token cache; modelled on gmail_live.py.
- `gcal_call(url, *, token, method='GET', data=None) -> (status, dict)`: tests replace this.
- `events(c, as_mailbox, calendar_id, start_utc, end_utc, *, q=None, limit=250, calendar=None) -> list[dict]`
  occurrences (singleEvents=true, orderBy=startTime, timeMin/timeMax, q). `calendar` = the occurrence's `calendar`
  object. Events without a summary the viewer may not see → `busy_only`. A 403/404 on the events list falls back to
  `freebusy` for that calendar and returns busy blocks (`busy_only` True). Out-of-office events → kind `away`.
- `event(c, as_mailbox, calendar_id, event_id) -> dict` (one occurrence).
- `calendar_list(c, as_mailbox) -> list[{id, name, primary, access_role, time_zone}]`.
- `time_zone(c, as_mailbox) -> str | None` (settings/timezone).
- `freebusy(c, as_mailbox, calendar_ids, start_utc, end_utc) -> dict[calendar_id, list[(start,end)]]`.
- `insert(c, as_mailbox, calendar_id, body, *, send_updates='all') -> dict`, `patch(c, as_mailbox, calendar_id,
  event_id, body, *, send_updates='all') -> dict`, `delete(c, as_mailbox, calendar_id, event_id, *,
  send_updates='all') -> None`. Writes carry `extendedProperties.private.tico_actor` (+ `tico_bot` for a bot) and use
  the full calendar scope; reads use `READ_ONLY_SCOPES['calendar']`.
- `connected(c, address) -> bool`: the company key is configured and delegated for calendar for that address
  (google_workspace authority), i.e. this person's home is Google.
- Errors: `Problem` codes `gcal_refused` 403, `not_found` 404, `gcal_unavailable` 502/503 retryable, like gmail_live.
- Personal connections (no company key) keep the computer path: widen the computer's snapshot from 24 h to the start
  of today + 14 days (connectors/mail/calendar.py `upcoming`, the targets payload, the ≤200 cap → ≤500).

`backend/calendar_derived.py` (derived): read-only, from existing tables.
- `runs(c, bot_ids, start_utc, end_utc, now) -> list[dict]`: one occurrence per `attempts` row overlapping the window
  (end = finished_at or settled_at, or now while running), `links.attempt`, `links.task`, `links.thread`,
  `links.automation` when the trigger names one.
- `scheduled(c, bot_ids, start_utc, end_utc, now) -> list[dict]`: enabled schedule automations expanded with
  `scheduler.next_due`, `planned` True, length = median of the last 10 `automation_runs` (min 15 min); anything firing
  more than hourly collapses to one all-day pill per day; bots that are away are skipped.
- `due(c, owners: list[tuple[str,str]], start_utc, end_utc) -> list[dict]`: open tasks' `due_at` owned by those
  teammates and `goal_kpis.deadline`, all-day.
- `group_runs(occurrences, min_minutes=10) -> list[dict]`: runs ≥ min stay; shorter ones that touch or overlap
  become one `runs:` block with `count` and `children`.
- `month_counts(c, bot_ids, start_utc, end_utc) -> list[{bot_id, date, count}]`.

`backend/reach.py` (reach): `may_book(c, actor: str, target: str) -> tuple[bool, str]` — `actor` is a bot actor
(`bot:<id>` or the hub's actor string), `target` a party (`human:<id>`/`bot:<id>`). Resolves `book` through the
same chain as `mode` (bot, its team, teams above, Everyone, default 'anyone'). 'nearby' = the same neighbors as the
`nearby` reach mode. A person's own Assistant and people are never limited. The reason names the setting.

## HTTP (track routes; `backend/calendar_api.py`, `install_calendar(app, store, mutate)` from app.py)

All under `/api/v2/calendar`. Callers: people, owner, bots. Reads use `store.read()`; writes use `mutate`.

- `GET /events?who=<refs, comma>&from=<ISO>&to=<ISO>&kinds=event,run,due,away` → `{from, to, calendars:[calendar
  view], occurrences:[...], month_counts?:[...], errors:[{ref, reason}]}`. Default who=me, kinds all, window ≤ 62
  days (422 beyond). Runs come only from bots in `who`: a person's calendar does not add the bots that report to them.
  One failing Google calendar → its error in `errors`, never an empty success. Windows > 7 days return
  `month_counts` instead of run occurrences.
- Calendar view: `{ref, type, id, name, home, time_zone, work_starts, work_ends, work_days, can_write}`.
- `GET /calendars?q=` → `{calendars:[calendar view + {kind: person|bot|shared|team|google}]}`: people, bots, shared,
  teams, and the viewer's own Google calendar list (google refs). No q = everything visible (bounded 500).
- `GET /search?q=&who=&from=&to=` → `{occurrences, partial: bool, errors}`; tokens in q: `with:<name>`, `bot:<name>`,
  `kind:<event|run|due|away>`, `before:<date>`, `after:<date>`, `has:transcript`, `automation:<name>`,
  `trigger:<kind>`, `longer:<N>m`. Default range −90 d … +180 d. Google: events.list q per calendar, calendars in
  `who` first, 8 at a time, 3 s budget. Tico: calendar_store.search. Meetings: existing meetings search joined via
  calendar_event_links. Logs latency + result count per search (an `H.event` or metric).
- `GET /free?who=&minutes=30&within=5d&from=` → `{slots:[{start,end}]}` within each person's working hours and zone,
  times only.
- `POST /events` body `{calendar: ref, title, start, end, all_day, time_zone, repeat, attendees:[ref|email],
  notes, location, add_meet, visibility}` → occurrence. Routing per spec §8.2: Tico home → calendar_store; Google
  home → gcal_live insert as that mailbox (needs Read and write + company `change_calendar`); a bot booking one
  connected person writes onto that person's Google calendar; several connected people → host = bot's manager if on
  it, else first connected attendee; only bots/unconnected → Tico. Every bot write checks `reach.may_book` for each
  person attendee (403 `book_rule` naming the rule). People (not bots, not the Assistant) get 403 `view_only` in v1
  except on their own Tico calendar when they have no Google (decision 13 lets the Assistant do it).
- `PATCH /events/{occurrence_id}` body fields + `scope` + `expected_sequence`; `DELETE /events/{occurrence_id}?scope=`.
- `POST /events/{occurrence_id}/response` body `{response}`.
- `GET /settings` / `PUT /settings` (the caller's own time_zone, work_starts, work_ends, work_days; time_zone from
  Google when connected and unset).
- `POST /shared` body `{name, time_zone}` (owner/admin), `PUT /shared/{id}/access` body `{grantee, level|null}`.
- `POST /absences` body `{human?: id, starts_on, ends_on, note}` (self, or owner/admin for anyone).
- Live: a new `calendar` topic in backend/events.py TOPICS; every Tico calendar write publishes `{calendar_id,
  occurrence_id?, event_id}` to it. The page refetches Google calendars on open, focus, and every 3 min while visible.
- Links: a live meeting started with `calendar_event: <occurrence_id>` (the Recorder sends it) records a
  `calendar_event_links` row (`meeting`); `links.meeting` is then filled on reads.
- Old routes (`/calendar/appointments`, `/calendar/upcoming`, actions) stay as they are.

## Access (spec §13)

- Google calendars: read as the viewer (`as_mailbox` = the viewer's Google address, `calendar_id` = the other
  person's address); Google's sharing decides. A bot reads a Google calendar only through a mailbox level it holds
  (connectors.calendar_allowed) or its operator's.
- Tico calendars: everyone in the company reads; `visibility: private` shows as Busy to non-attendees. Write: owner,
  a bot's managers (`Auth.bot_manager`) for that bot, grantees with read_write, owner/admin for shared.
- Bots booking people: `reach.may_book`. `free` returns times only to anyone who may book.

## CLI + MCP (track cli)

`clients/hubcli.py`, `clients/hubtools.py`, over the HTTP routes above; old `list/schedule/status` keep working
(`tico calendar list` with no new flags behaves as before).
- `tico calendar list [--who a,b] [--from ISO --to ISO] [--kinds ...] [--json]`
- `tico calendar free --who a,b --minutes 30 [--within 5d]`
- `tico calendar add --title … --start … --end … [--who …] [--repeat "weekly on mon"] [--calendar <ref|legal@…>]
  [--all-day] [--notes …] [--no-meet]`
- `tico calendar move <occurrence> --start … [--end …] [--scope this|following|all]`
- `tico calendar cancel <occurrence> [--scope …]`
- `tico calendar search "<words>" [--who …]`
- MCP tools: `tico_calendar_events`, `tico_calendar_free`, `tico_calendar_add`, `tico_calendar_move`,
  `tico_calendar_cancel`, `tico_calendar_search` (existing three stay).

## UI (track ui; TypeScript, strict)

- `ui/app/calendar.ts` (page, route `#/calendar`, query `?who=…&view=day|3day|week|month|list&date=YYYY-MM-DD&q=…`),
  `ui/app/calendar-row.ts` (sidebar row), `ui/styles/calendar.css`; registered in `ui/app/router.ts` and
  `ui/index.html`. Spec §12.
- Sidebar order (decision 2): Finish setup (when shown), Tasks, Calendar, Threads, Updates, Company (Strategy,
  Finance, Growth, Automations, Rules), Library (Docs, Files, Skills, Tools, Meetings), Pipelines pins, Team.
  Directory leaves Library: an icon on the Team header opens `#/library/directory`. The Rules folder becomes one
  Rules row under Company (its Internal/External/Due/Suggested pages stay reachable from the Rules page).
- Join (row, bubble, page, card): opens `meeting_url` in a new tab and, on a Mac desktop browser or the Tico desktop
  app, `tico-recorder://record?server=<origin>&title=<title>&event=<occurrence_id>` (Recorder on by default; a
  per-person "Record calls I join" toggle in the page's ⋯ menu, stored in localStorage + `/calendar/settings`
  field `record_on_join` default true).
  The Recorder records only an occurrence `GET /events/{occurrence_id}` shows with `can.join`, from 15 min before
  its start to its end, under that title; signed in already it starts at once, after a sign-in it asks "Record
  <title>?" once, as for a Tico call (meet-calls contract).
- Who bots may book: one control on Rules > reach (track reach).

## Rules for every track

- Repo ticoteam/tico. Work only in your own worktree. Never `npm ci`/`npm install` (node_modules is a shared symlink).
  Never push, never open a PR, never touch another track's files unless this contract assigns them to you.
- Commit on your branch with the repo's git identity (no Co-Authored-By, no "Claude" author), message from your own
  file `/Volumes/PNYRP60PSSD/orca/workspaces/ticoteam-tico/_commitmsg-calendar-<track>.txt`.
- Checks: `ruff check <changed .py>`, `ruff format --check <changed .py>`, the pytest files you wrote or touched
  (run one file at a time, `-p no:xdist` if needed), UI: `bash scripts/check_ui_types.sh` and your one browser script
  (`node scripts/ui-tests.cts <script>` or as ui/tests docs say). No full suites; the machine is shared.
- Tests guard real behaviour (permissions, recurrence maths, routing, data safety); no tests of wording.
- No test calls Google or opens Chrome from pytest. Google is faked by replacing `gcal_call`.
- Comments explain why, briefly, in the codebase's style. UI copy minimal: titles only, marks not words, no "Filter".
- When done, write `CHANGES-calendar-<track>.md` at your worktree root (not committed): what you built, files,
  tests run with results, anything you could not do, and any change you needed to this contract.
