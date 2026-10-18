# Tico HQ

Tico HQ is the small public service the Tico team runs at `https://updates.tico.team`. It is not part of a team's install.
An install reaches it only through its own server, and only for what is listed here; [PRIVACY.md](../PRIVACY.md) is what an
owner reads about it. This page is HQ's public API: what an install calls and what HQ stores. HQ's source and hosting now
live in the private ticoteam/tico-services repository.

## Endpoints

| Endpoint | What it is for | Stores |
|---|---|---|
| `GET /v1/latest` | The release check. With the anonymous usage count on, it carries five fields (a random install id, the version and three yes/no activity flags) and answers the latest release, as GitHub would | One row per install id: first and last day seen, last version, last active days, the first day a bot finished a turn |
| `GET /v1/stats` | The public aggregate of that count | Nothing |
| (in `GET /v1/latest`) | Tico's short messages for given versions and its known issues by version, the same lists for every install ([Messages, known issues and fixed-in notices](#messages-known-issues-and-fixed-in-notices)) | Nothing from installs |
| `POST /v1/recruit` | Bot suggestions for one group while a team builds its team chart ([Finish setup](onboarding.md#the-team-builder)) | Nothing |
| `GET /v1/onboarding/health`, `POST /v1/onboarding/event` | The setup page on tico.team: whether the assistant can answer, and the page's download count ([below](#the-setup-page-v1onboarding)) | With counts switched on, one row of counts per planning session; no text |
| `/v1/support/...`, `/v1/staff/...` | Support tickets ([routes](telemetry.md#support-tickets), [support.md](support.md#what-is-sent-and-kept)) and support access, below | The tickets; access sessions and their request log |
| `GET /healthz` | Liveness | Nothing |

## Support access

A person lets the Tico team into their app from a ticket ([support.md](support.md#letting-tico-support-in)). The install
opens and ends the session and fetches the work; HQ never connects to an install.

| Route | Who | |
|---|---|---|
| `POST /v1/support/{ticket}/access` | the install (`X-Ticket-Secret`) | `{tier: app\|full, expires_at}` opens a session: `{session}`. A live one on the same ticket ends |
| `POST /v1/support/{ticket}/access/{session}/pull` | the install | held up to 25 s: `{ended, requests: [{rid, by, method, path, query, content_type, body_b64}]}` |
| `POST /v1/support/{ticket}/access/{session}/reply` | the install | `{replies: [{rid, status, content_type, body_b64}]}` |
| `POST /v1/support/{ticket}/access/{session}/end` | the install | the person stopped it |
| `GET /v1/staff/access` | staff key | live sessions (`?ticket=TK-...`: that ticket's, ended ones too) |
| `GET /v1/staff/access/{session}/log` | staff key | each carried request: when, by, method, path, status |
| `GET\|POST\|PUT\|PATCH\|DELETE /v1/staff/access/{session}/api/{path}` | staff key | one request to the install's `/api/{path}`, answered with its status and body; `X-Tico-Support-By: staff` (default) or `support-agent`; 409 for a session that ended or ran out, 504 when the install does not answer in 30 s |

The queue between a staff call and the install's pull is in HQ's memory (HQ runs as one process). HQ stores the session
(ticket, tier, opened, expires, ended) and the one-line record of each request, deleted with the ticket; never a request or
answer body. What an install answers is untrusted data, for staff and for the Support Agent.

## `POST /v1/recruit`

```
{"department": "sales", "briefing": "Inbound demos and a few big accounts", "catalog_version": "ee9999fd8f2a",
 "about": {"what": "We sell scheduling software to clinics", "sells_to": "businesses", "software": true},
 "install_id": "<optional v4 UUID>"}
-> 200 {"bots": [{"template_id": "sales-lead", "why": "Heads Sales and reports to you"}, ...],
        "suggested_default": ["sales-lead"], "source": "model" | "local", "catalog_version": "ee9999fd8f2a"}
```

- **Input** is checked strictly: `department` is a group in the templates, `briefing` and `about.what` are strings of at most 500 characters,
  `sells_to` is `businesses`, `consumers`, `both` or empty, `software` is a boolean, `install_id` (optional) is a v4 UUID, and no other
  field is allowed. Anything else is `422 {"error": "invalid"}`, with nothing echoed back. A body over 4 KB is refused.
- **Answer.** Template ids from that group only, the head first, at most 8, each with a why of at most 140 characters, plain
  text on one line. The why is shown to a human; no bot follows it.
- **Model.** OpenAI GPT-6 Luna (`OPENAI_API_KEY`; `HQ_RECRUIT_MODEL` overrides the model) through the Responses API with a JSON schema
  whose template ids are an enum, `store: false`, a 4.5 second timeout and a short prompt holding only the group, its templates
  (id, name, summary, tags), the answer and the three facts. Every id is checked again against the templates.
- **Spend cap.** At most `HQ_RECRUIT_DAILY_CAP` model calls a UTC day (default 2000), counted in memory. Past it, with no key, or on any
  model failure, HQ ranks locally (`source: "local"`) with the same recommender every install has.
- **Cache.** A model answer is kept in memory for an hour under a SHA-256 of the question and the template version; a repeat costs nothing.
- **Rate limits.** In memory, as salted hashes: 30 requests an hour per address and 100 a day per install id, inside HQ's limit of 60
  requests an hour per address for every route. Over any is `429`.
- **Logs.** Nothing logs a request or its body. A failed model call is logged as its exception type only.

### The templates HQ reads

HQ has no templates of its own. It is built from a published Tico release: the groups and each card's template, name, group, icon,
tags, suggest level, one-sentence summary and head flag (no instructions), made with `build()` in `backend/recruit_rank.py`,
and a copy of that recommender. The Tico server builds its own template list with the same function, so the two agree on
`catalog_version` for that release. At the release's tag, `python3 scripts/build_catalog_json.py --out DIR` writes into DIR
`catalog.json`, `onboarding-actions.json` (the setup page's browser planner: the draft engine's actions, vocabulary and
limits, the role list and the starter pack), `recruit_rank.py` and `company_file/` (the draft engine, whose `keys.py` is the
scrubber for setup-website attachments); `--out DIR --check` exits 1 when DIR is stale.

## The setup page: `/v1/onboarding`

The page at [tico.team/setup](https://tico.team/setup) calls these from the visitor's browser. The
assistant that answers there comes later; these routes work without it, so the page's download count can be measured
in the browser alone first.

```
GET  /v1/onboarding/health -> 200 {"ok": false}       (true once the assistant can answer)
POST /v1/onboarding/event  {"session_id": "<random id>", "kind": "download"}
                           {"session_id": "<random id>", "kind": "message"}
                           {"session_id": "<random id>", "kind": "step", "step": 2}
                           -> 204, whether or not anything is kept
GET  /v1/staff/onboarding?days=14   (staff key) -> {"counting": bool, "days": [{"day", "sessions",
                           "sessions_2plus_messages", "sessions_2plus_messages_downloaded", "downloads",
                           "cost_usd"}]}
```

- **Input** is checked strictly: a session id of 8 to 64 letters, digits, `-` or `_`, refused before it is hashed when
  it is shaped like a key or token (a provider prefix such as `sk-` or `ghp_`, or 32 or more characters mixing letters and
  digits that are not a UUID), so nothing derived from a pasted key is kept; kind `download`, `message` or `step`; `step` a
  whole number from 0 to 9, only with kind `step`; no other field; at most 512 bytes. Anything else is `422 {"error": "invalid"}`.
  A `text/plain` body (a beacon) is read the same way.
- **What is kept.** With `HQ_ONBOARDING_COUNTS=on`, one row per session in `onboarding_sessions`: a salted hash of the
  session id (the salt is in the database, so a restart keeps the row), the first UTC day, the furthest step, whether
  the file was downloaded, how many messages the visitor sent (a count, sent once per message by the page), and the
  turns and cost the assistant adds. No message text and no address; the table has no
  column for them. Off (the default), no visitor data is written. At most 20,000 new sessions a day are counted. Rows go 13
  months after their day, with the daily purge, which also runs at start. PRIVACY.md says the same in the page's words.
- **Browsers.** Only the origins in `HQ_ONBOARDING_ORIGINS` (comma-separated, default `https://tico.team`) get
  `Access-Control-Allow-Origin` back, on every answer under `/v1/onboarding`, including HQ's `429`, so the page can read
  it. A preflight (`OPTIONS`) for `GET` or `POST` with at most `Content-Type` is answered `204` before the request limit and
  is not counted against it. Staff routes send no CORS headers.
- **Request budget.** `POST /event` is left out of HQ's 60 requests an hour per address, so a long chat still leaves room
  for its download count, and has its own limit of 300 an hour per address (in memory, salted, as HQ's other limits).
  `/health` stays under the shared limit.
- **Staff read** for the Customer Insights Analyst's report: totals per UTC day, today last, 1 to 90 days (default 14).
  `sessions_2plus_messages_downloaded` over `sessions_2plus_messages` is the main measure; `downloads` is every download,
  for cost per downloaded file.

## Messages, known issues and fixed-in notices

The answer to `GET /v1/latest` also carries `notices` (`{id, versions, body, url}`: a message for a release such
as `0.3.50` or a whole line such as `0.2`) and `known_issues` (`{id, title, since, fixed_in, url}`: in `since` and later, until
`fixed_in`). Every install gets the same lists and picks its own version's, so nothing more is sent or stored for them. An install
shows its version's newest message once to each person, and Help lists **Known issues in your version** (or says it could not
check, when its update check goes to GitHub instead: usage counting off, or a release mirror).

A release before v0.3.52 keeps only the release fields. When one of those names its version (usage counting on), HQ puts its
message's `url` in place of the release link, which is what its **New version · View changelog** opens. A v0.2 install with
counting off asks GitHub and cannot be reached this way.

The team writes them with the staff key (`Authorization: Bearer $HQ_STAFF_KEY`):

- `POST /v1/staff/notices` `{"versions": "0.2", "body": "You're on 0.2: update to get one-tap support.", "url": "https://..."}`
  (body at most 280 characters, an https link optional); `GET` lists them, `DELETE /v1/staff/notices/{id}` stops one.
- `POST /v1/staff/known-issues` `{"title": "...", "since": "0.3.50", "ref": "#712", "url": "https://..."}`;
  `POST /v1/staff/known-issues/{id}` `{"fixed_in": "0.3.52"}`; `GET` and `DELETE` as above.
- `POST /v1/staff/tickets/{id}/fix` `{"ref": "#712"}` links a request to the ticoteam/tico issue or pull request that will fix it
  (`software/hq-tickets link` in the Support Agent).

**Fixed-in, by itself.** Every 15 minutes a staffed HQ looks at the latest release. When the tag changes it reads the commits since
the tag it last saw (GitHub's compare API, every page of it; `GITHUB_TOKEN` raises the rate limit, `HQ_COMPARE_URL` points
elsewhere) and collects what they say they fix: the pull request a merge commit names, a squash merge's trailing `(#123)`, and
`fixes`, `closes` or `resolves #123`. A bare `#123` is not a fix, and a revert fixes nothing. On every open or answered request
linked to one of them it posts `Fixed in vX, update here: <release page>`. A known issue with that `ref` gets `fixed_in`. A request
gets the notice once; a closed one gets none, and one held as spam gets it only if a person releases it (by correcting the verdict),
then, once. The first tag an HQ sees is only remembered, and GitHub not answering, or not giving every commit, means the next look
tries again. `POST /v1/staff/releases` `{"tag": "v0.3.52", "refs": [712]}` posts for those refs by hand and leaves the release's other
commits to the watch; without `refs` it reads the commits and counts as the watch's look.

