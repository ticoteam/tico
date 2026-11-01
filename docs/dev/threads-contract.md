# Threads contract (C1-C5, the write door, schema, events)

The shared contract for the native threads build (spec: `_threads-plan.md` v3.3, §1, §2, §3.1, §3.5, §4). Every
track builds against this page. Owner: `threads/core`. A change here is announced in that branch's commit message
("contract:"), and additive changes are preferred over renames.

Terms in UI and docs: **Thread, Home, DM, Sub-thread, Call**. Internally the table stays `conversations` and a
thread id **is** a conversation id. `bots.thread_id` and `turns.thread_id` are provider sessions: never read or
reuse them as thread ids.

## 0. Files core owns

Don't edit these except where your brief says so; ask core in your report.

| File | What core owns |
|---|---|
| `backend/threads.py` (new) | everything: schema `ensure`, C1-C5, `post`, `create`, read state, edit/delete, migration |
| `backend/hubdb.py` | the conversation and message write paths: `open_conversation`, `say`, `_write_message`, `answer`, `notice`, `mark_delivered`, `mark_read`, `task_comment*`, `inbox*`, `undelivered`, the conversation/message columns in `migrate` |
| `backend/auth.py` | `Auth.conversation`, `Auth.run_conversation`, `Auth.tico_principal`, `_own_ask_to_a_person` (~lines 940-1060) |
| `backend/events.py` | `_Viewer`, `_messages`, `_readable_before_deletion`, the `messages`/`threads` entries of `TRIGGERS`, the one `threads` hook line in `read()` |
| `backend/sql.py` | the `conversations` and `messages` predicates in `guarded()` |
| `backend/rooms.py` | all |
| `clients/sessions.py` | all |
| `runner-rs/src/execute.rs` | `session_key`, `clear_epoch` and the session lookup/adoption around them |

## 1. Schema

All columns are added with `H.add_column` on every start (`threads.ensure(c)`, called from `hubdb.migrate` and from
the cloud store's unversioned boot list), the pattern the latest releases use. The one-time data migration (§7) is
recorded under `registry_metadata` key `threads-migration` and runs inside one transaction.

### 1.1 `conversations` (a thread)

Existing columns keep their meaning: `subject` = **title**, `kind`, `task_id`, `participants_json`, `scope` (legacy,
read for one release), `owner_actor`, `room_key`, `created`, `last_message_at`, `closed_at`.

```sql
ALTER TABLE conversations ADD COLUMN audience TEXT;            -- company|team|members|task|home|personal|participants|meeting
ALTER TABLE conversations ADD COLUMN audience_group_id TEXT;   -- the group for audience='team'
ALTER TABLE conversations ADD COLUMN parent_id TEXT;           -- parent thread (NULL for a root)
ALTER TABLE conversations ADD COLUMN parent_message_id TEXT;   -- the message this sub-thread branched from
ALTER TABLE conversations ADD COLUMN root_id TEXT;             -- the root thread; = id for a root
ALTER TABLE conversations ADD COLUMN path TEXT;                -- '/<root>/<child>/.../<id>/' (materialized ancestors + self)
ALTER TABLE conversations ADD COLUMN last_seq INTEGER NOT NULL DEFAULT 0;         -- activity counter
ALTER TABLE conversations ADD COLUMN last_unread_seq INTEGER NOT NULL DEFAULT 0;  -- human-or-addressed counter
ALTER TABLE conversations ADD COLUMN archived_at TEXT;
ALTER TABLE conversations ADD COLUMN updated_at TEXT;
-- owner_actor already exists and is reused:
--   home     -> the Home's owner: 'bot:<slug>' | 'human:<id>' | 'group:<id>'
--   personal -> the DM's starter (migrated personal rooms: their owner, as today)
--   members/company/team -> the starter
CREATE UNIQUE INDEX threads_parent_message ON conversations(parent_message_id) WHERE parent_message_id IS NOT NULL;
CREATE UNIQUE INDEX threads_one_home ON conversations(owner_actor) WHERE audience='home' AND closed_at IS NULL;
CREATE INDEX threads_root ON conversations(root_id);
CREATE INDEX threads_path ON conversations(path);
CREATE INDEX threads_recent ON conversations(root_id, last_seq) WHERE archived_at IS NULL;
```

- Sub-threads **inherit** `audience`, `audience_group_id` and `task_id` from the root at creation and follow any
  later root audience change (one UPDATE by `root_id`). A sub-thread is never more open than its root.
- The parent is set once. `parent_message_id` is unique, so two concurrent "Reply in sub-thread" on one message
  produce one sub-thread (the loser reads the winner's row, §3.2).
- `path` lets `?thread=<id>` follow a subtree: `path LIKE '<thread path>%'`.

### 1.2 `messages`

Existing columns keep their meaning. `to_actor` is now **nullable and means "addressed to"** (the primary mention,
ask target, or DM recipient). `delivered_at`/`read_at` on the row stay the primary recipient's state, mirrored from
its delivery, for one release.

```sql
ALTER TABLE messages ADD COLUMN seq INTEGER;                       -- per-install order and SSE cursor (§5)
ALTER TABLE messages ADD COLUMN origin_id TEXT;                    -- '<uuidv7>@<install id>' (§6)
ALTER TABLE messages ADD COLUMN revision INTEGER NOT NULL DEFAULT 0;
ALTER TABLE messages ADD COLUMN restricted INTEGER NOT NULL DEFAULT 0;  -- needs the per-message check (§2.3)
ALTER TABLE messages ADD COLUMN legacy_audience TEXT;              -- 'shared:<slug>' on migrated shared-room history
-- edited_at, deleted_at already exist
-- Not unique: an insert that copies another row's seq or origin_id must not fail; the stamp trigger replaces both
-- (a copied origin also marks the row restricted). seq is unique by the sequence; (origin_id, revision) by
-- message_revisions.
CREATE INDEX messages_seq ON messages(seq) WHERE seq IS NOT NULL;
CREATE INDEX messages_origin ON messages(origin_id) WHERE origin_id IS NOT NULL;
CREATE INDEX messages_thread_seq ON messages(conversation_id, seq);
```

A **carrier** is a keeper notice that starts a run for a second woken bot (§4.4). It has `refs.carries=<message id>`
and **no `seq`**: timelines, counters, search and SSE thread views never show it.

### 1.3 New tables

```sql
-- Access membership (C2). Only for audience members|personal|participants. actor: 'human:<id>' | 'bot:<slug>' |
-- 'group:<id>' (resolved live, people and bots, nested) | 'peer:<install>' (a linked install) |
-- '<human|bot>:<name>@<install>' (a remote guest, federation).
CREATE TABLE IF NOT EXISTS thread_members (
  id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, actor TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('owner','member')),
  added_by TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE (thread_id, actor));
CREATE INDEX IF NOT EXISTS thread_members_actor ON thread_members(actor, thread_id);

-- Attention and read state (C2, C4), per (thread, reader). A row may exist with following=0 (read state only).
CREATE TABLE IF NOT EXISTS thread_followers (
  id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, actor TEXT NOT NULL,
  following INTEGER NOT NULL DEFAULT 1 CHECK (following IN (0,1)),
  notify TEXT NOT NULL DEFAULT 'live' CHECK (notify IN ('live','mentions','digest','muted')),
  reason TEXT NOT NULL DEFAULT 'manual',      -- posted|mentioned|task|joined|manual|starter
  last_read_seq INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE (thread_id, actor));
CREATE INDEX IF NOT EXISTS thread_followers_actor ON thread_followers(actor, following, thread_id);

-- The inbox (C3): one row per (message, recipient), written in the post's transaction.
CREATE TABLE IF NOT EXISTS message_deliveries (
  id TEXT PRIMARY KEY, message_id TEXT NOT NULL, recipient TEXT NOT NULL,
  thread_id TEXT, root_id TEXT,
  mode TEXT NOT NULL CHECK (mode IN ('steer','queue','defer','digest','none')),
  reason TEXT NOT NULL,                        -- §4.2
  cause TEXT NOT NULL DEFAULT 'human',         -- human|bot|keeper|outside: who caused this wake (loop guard)
  state TEXT NOT NULL DEFAULT 'pending'
    CHECK (state IN ('pending','delivered','read','capped','held','failed','moot')),
  revision INTEGER NOT NULL DEFAULT 0,         -- the message revision this delivery last carried
  carrier_id TEXT,                             -- the carrier message that started the run (§4.4)
  attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at TEXT, error TEXT,
  delivered_at TEXT, read_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE (message_id, recipient));
CREATE INDEX IF NOT EXISTS message_deliveries_waiting ON message_deliveries(recipient, created_at)
  WHERE state IN ('pending','delivered','capped','held');
CREATE INDEX IF NOT EXISTS message_deliveries_capped ON message_deliveries(root_id, created_at) WHERE state='capped';
CREATE INDEX IF NOT EXISTS message_deliveries_loop ON message_deliveries(root_id, created_at)
  WHERE cause='bot' AND recipient LIKE 'bot:%' AND mode IN ('steer','queue');
CREATE INDEX IF NOT EXISTS message_deliveries_outside ON message_deliveries(state, next_attempt_at)
  WHERE recipient LIKE 'slack:%' OR recipient LIKE 'peer:%';

-- Who a message names (C2). Mentions never grant access; in_audience=0 rows produced a warning to the author.
CREATE TABLE IF NOT EXISTS message_mentions (
  id TEXT PRIMARY KEY, message_id TEXT NOT NULL, actor TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('mention','ask','assign')),
  in_audience INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL,
  UNIQUE (message_id, actor));
CREATE INDEX IF NOT EXISTS message_mentions_actor ON message_mentions(actor, created_at);

-- Durable (origin_id, revision) dedupe and edit history (C5), for the life of the message.
CREATE TABLE IF NOT EXISTS message_revisions (
  id TEXT PRIMARY KEY, message_id TEXT NOT NULL, origin_id TEXT NOT NULL, revision INTEGER NOT NULL,
  body TEXT, deleted INTEGER NOT NULL DEFAULT 0, actor TEXT NOT NULL, created_at TEXT NOT NULL,
  UNIQUE (origin_id, revision));
CREATE INDEX IF NOT EXISTS message_revisions_message ON message_revisions(message_id, revision);

-- Reactions (spec section 3.4). One row per actor and emoji; react needs read access to the message (C1 plus the
-- per-message check), and only the reactor removes their own.
CREATE TABLE IF NOT EXISTS message_reactions (
  id TEXT PRIMARY KEY, message_id TEXT NOT NULL, actor TEXT NOT NULL, emoji TEXT NOT NULL, created_at TEXT NOT NULL,
  UNIQUE (message_id, actor, emoji));
CREATE INDEX IF NOT EXISTS message_reactions_actor ON message_reactions(actor, created_at);

-- Per-install counters. name='messages' is the message seq (initialised to MAX(messages.rowid)).
CREATE TABLE IF NOT EXISTS sequences (name TEXT PRIMARY KEY, value INTEGER NOT NULL);
```

## 2. C1: authorization, one predicate, three renderings

### 2.1 Audiences

| audience | Who reads (humans) | Notes |
|---|---|---|
| `company` | every company human | |
| `team` | people in `audience_group_id` and its sub-groups, plus the company owner | bots per §2.2 (a group names its bots too) |
| `members` | `thread_members` actors; a `group:<id>` member resolves live | hidden: not listed, not searchable, no existence leak (404) |
| `task` | whoever may read the task (`auth.task`, `task_private_readable`) | today's task-discussion rule |
| `home` | the Read audience of `owner_actor` (bot, person or group), its managers (Manage ⇒ Read) | posting needs Read **and** Write on the owner |
| `personal` | `owner_actor` and `thread_members`; **no owner bypass**; the Strategy owner-seat rule stays | all new DMs; migrated personal/Assistant/Librarian/Strategy rooms |
| `participants` | actors in `participants_json`, plus the company owner (today's inspection) | migrated `direct` rows only |
| `meeting` | `live_privacy.require_conversation` as today | migrated meeting rooms (`room_key` starts `live_privacy.ROOM_PREFIX`) |

Sub-threads are evaluated through their **root** row (same audience by §1.1, but the root is the authority).

### 2.2 Bots
Bots are AI employees: an internal bot reads what a person in the same seat reads.
- **Every bot** reads by access membership (`thread_members`, including via a group), the participant grant
  (`participants_json`), `home` when it is the Home's owner or holds Read on its owner, or a task thread it may read
  (`auth.task`). Its own open asks to a person stay readable (`_own_ask_to_a_person`). `members`, `personal` and
  `participants` threads open to nothing else.
- **Internal bots** (runner token, any turn) also read every `company` thread, and the `team` threads of the group
  they are in or any group above it (`Viewer.chain`, nested groups included). The turn's grant (`attempt_conversations`,
  any thread with the same `root_id`) adds the woken tree when the bot could not otherwise read it. A legacy root (no
  stored audience) still opens to an internal bot only by that grant, and migrated shared-room history only to the
  room's bot, a participant or a granted turn.
- **External agents** (agent token) never get `company` or `team` standing read: there only their participation
  counts. A bot in a thread's wake audience (`threads.audience_has`: company bots, the group's bots) may still be
  woken there.
- **Posting in a Home** takes Write on its owner for bots as for people (`can_post`). Only the Home's own bot and a
  run replying into the thread its attempt was granted skip it.
- There is no `threads: read_open`. Membership is the grant.
- The Assistant acts with its person's rights (`auth.tico_principal`); the Librarian keeps its principal.

### 2.3 Per-message checks (inside the audience)
- Unchanged rule: `task_privacy.message_readable` (meeting provenance, referenced private tasks, bot limits).
- **`restricted`** is computed at write time: 1 when the message has `legacy_audience`, an `in_reply_to`, any
  `refs` key outside `threads.SAFE_REF_KEYS`, structure under a safe key other than one level under
  `threads.NESTED_REF_KEYS` (`origin`, `mentions`, `warnings`), any string in refs that names a task (`task:`, a task
  link, or any task's id), sits in a `task` or `meeting` thread, or attaches files. The migration computes the same in
  SQL (`json_tree`). A message with
  `restricted=0` skips `message_readable` once the audience check passed (the SSE and timeline fast path).
- **`legacy_audience='shared:<slug>'`**: readable by exactly today's live predicate, no more and no less:
  `rooms.shared_member(c, auth, actor, slug)` with bot Read for people; participants and the room's bot for an
  agent; a run's grant. Home readers outside it see one collapsed **Earlier messages** row (`{"id", "seq", "hidden":
  true, "earlier": true}` in a timeline, consecutive ones as one; nothing on SSE). `thread.earlier` (`{"count"}`) on
  the Home's root is the bot's manager's alone, whether or not they read that history.
- **A migrated Home** (the bot's open shared room, `audience='home'` stored): everything new (`legacy_audience`
  NULL, any sub-thread) follows Home Read. An old reader without Home Read still opens the Home's root
  (`threads.legacy_reader_only`) but sees only that history: the timeline lists only legacy messages, `last_seq`
  and `last_message_at` are the history's, unread is 0, no sub-thread opens, and SSE, `hub sql` and the legacy
  per-message check (`task_privacy.message_readable` → `threads.legacy_gate`) hide new messages from them.
- A hidden message renders as a quiet "not shown" row in timelines and is skipped on SSE.
- New posts can't widen anything (`H.path_mentioned`, `guard_write` keep running in the write door).

### 2.4 Entry points (`backend/threads.py`)

```python
viewer = threads.viewer(auth, who)          # per-request: Identity + Auth + caches (never across requests)
threads.readable(c, viewer, thread_or_message) -> bool
    # thread row/dict/id: the audience predicate on its root. message row/dict/id: its thread's predicate, then
    # legacy audience, then (restricted only) task_privacy.message_readable.
threads.require(c, viewer, thread_id, *, write=False) -> thread row   # 404 when unreadable (never 403: no leak)
threads.denial(c, viewer, row) -> None | (404, "not_found", msg)      # Auth.conversation asks it: unreadable = 404;
                                                                       # a person who left / sign-in off reads nothing
threads.can_post(c, viewer, thread) -> bool                            # read + audience post rule (home: Write too)
threads.can_manage(c, viewer, thread) -> bool                          # starter/owner member, or admin
threads.readable_sql(c, viewer, alias="conversations") -> str          # SQL boolean over a conversations row
threads.message_sql(c, viewer, alias="messages") -> str                # legacy-audience term for a messages row
threads.readable_ids(c, viewer, ids) -> set                            # the SSE rendering: one query over ids
threads.AUDIENCES, threads.PRIVATE_AUDIENCES, threads.SAFE_REF_KEYS
```

- **Python** rendering: `threads.readable` (and `Auth.conversation`, which now calls it).
- **`hub sql`** rendering: `sql.guarded()` uses `readable_sql` for `conversations` and `message_sql` for `messages`.
- **SSE** rendering: `events._Viewer.conversation` uses `readable_ids` (one query per batch over the changed ids).
- One fixture (`backend/tests/test_threads_privacy.py`) runs the privacy matrix through all three and asserts
  they agree.
- Teammate levels come from `backend/teammate_access.py` (`threads/perms`): `levels_for`, `can`,
  `group_member_actors`, `readers`, `may_reply`. Until it merges, `threads.py` holds a thin shim with today's bot
  behaviour and Everyone for people and groups.

## 3. Writes

### 3.1 The write door

```python
threads.post(c, principal, thread_id, body, *, kind="say", refs=None, parent_message_id=None, to_actor=None,
             via=None, origin=None, checked=None, attachments=None, as_actor=None, start=None) -> message view
```
- `c`: inside the caller's write transaction (`mutate`/`store.transaction`). `principal`: a `threads.viewer(...)`, or
  an actor string for system writes (`keeper`, a migration) which skip C1.
- `thread_id`: the thread written to. With `parent_message_id`, the post goes to that message's sub-thread, created
  if it has none (§3.2), and `thread_id` must be the message's thread.
- `kind`: `say|ask|answer|notice|steer`. `to_actor`: the explicit addressee (optional); otherwise the first
  in-audience mention or ask target, otherwise NULL.
- `via`: who really acts (`assistant`, `botops`, `slack`, `peer`), recorded in `refs.via`. `origin`:
  `{"origin_id", "revision", "install", "actor"}` for replicated inbound (Slack, federation); a repeat
  `(origin_id, revision)`, once the writer is authorized, returns the existing message (`duplicate: true`) and writes
  nothing. A repeat whose message is in another thread or from another signer is 409 `origin_taken`.
- `checked`: the org-rule verdict from `org_rule_check.preflight` (step 5, done by the caller **outside** the
  transaction, operation `threads`). `post` applies it with `org_rule_check.apply(c, checked, body, replay)` (the
  payload is the body text) and reads **apply's return**: held → returns `org_rule_check.answer(gate)` and writes no
  message and no delivery; warn → the warned text is written; moved → 409 `moved` (retryable). `post` builds
  `replay = {"thread_id", "body", "kind", "refs", "parent_message_id", "to_actor", "via", "origin", "attachments",
  "start"}` from its own arguments: what Send anyway runs (the rules-people track's `threads` replayer: `threads.post`
  again as the person it was held for, the check skipped, passing `start` through too).
- A rule that starts matching only because the audience widened after `preflight` is not caught: with no verdict,
  `apply` has nothing to compare, and someone added just after the write gets the same exposure anyway.
- **A held message makes nothing.** A post that would make its thread is checked before anything is written, and
  only a message that goes makes the thread: `parent_message_id` naming a message with no sub-thread yet (the
  sub-thread is made after the verdict), or `start` with `thread_id=None`: either the new thread's `create`
  arguments (`{"title", "audience", "audience_group_id", "members", "task_id", "kind"}`) or `{"dm": [members],
  "title"}` (reuses the open DM with exactly those members, else makes one). A thread made with its first message is
  always `post(..., start=...)`, never `create`/`dm` followed by `post`: a held first message would leave an empty
  thread behind. In the replay a `start` post has `thread_id: null`.

Step order (§3.1): (1) the caller authenticated `principal` (actor, represented principal, provenance); (2) C1 post
right (`can_post`); (3) server mention parse (`threads.mentions(c, thread, body)`); (4) screening for outside
authors (the caller's door: Slack/peer); (5) rule preflight, outside the transaction, by the caller; (6) in the
transaction: revalidate rights and audience, apply the verdict, write the message, mentions, deliveries, counters,
followers and events; (7) commit rings the doorbell (`changes` rows from triggers).

The legacy adapters keep working and pass through the same internals: `hubdb.say` (with a `to_actor`),
`hubdb.task_comment`, `hubdb.answer`, `hubdb.notice`, runner replies, `/api/v2/messages`, `/api/v2/chat/{bot}`,
`/conversations/*`. All of them end in `hubdb._write_message`, which calls `threads.stamp(...)` before the insert
(seq, origin, revision 0, restricted, root stamping) and `threads.record(...)` after it (mentions, deliveries,
counters, followers, loop guard).

### 3.2 Creating threads
Return values: `post` returns the message dict (`id`, `conversation_id` = thread id, `seq`, `origin_id`,
`revision`, `refs`, ...; `duplicate: true` for a repeated origin). `create`, `dm`, `set_audience` and `update` return
the thread view (§10, with `id`). `home` and `sub_thread` return the stored row (`id`, `subject`, `audience`,
`root_id`, `path`, ...). Every one of them has `id`.

```python
threads.create(c, principal, *, title="", audience="members", audience_group_id=None, members=(),
               parent_message_id=None, task_id=None, kind="chat") -> thread view
threads.dm(c, principal, members, *, title="") -> thread view      # audience='personal', reuses an open DM with exactly these members
# With a first message: threads.post(c, principal, None, body, start={...create args...} or {"dm": [...]}) (§3.1).
threads.home(c, owner_actor, *, create=True) -> thread row          # lazy Home for bot:/human:/group: (a bot's open shared room is its Home); None when create=False and none
threads.sub_thread(c, principal, parent_message_id) -> thread row   # get-or-create, race-safe on the unique index
# A new sub-thread's `subject` is threads.sub_thread_title(parent body): first line, no markdown or @mentions,
# clipped near 60 chars at a word with "…". Stored once; editing the parent later leaves it.
```
- Roots default to `members` with the starter as `owner` member. Making a root public (`team`/`company`) is
  `threads.set_audience` by its starter or an admin (no Write needed on the group).
- Adding a member needs **Write** on them (`teammate_access.can(c, viewer, "write", target)`), except the reply-back
  rule (`may_reply`).
- A bot may create roots it is a member of and sub-threads anywhere it may post.

### 3.3 Membership and attention

```python
threads.add_member(c, principal, thread_id, actor, *, role="member")
threads.remove_member(c, principal, thread_id, actor)          # thread owner or admin; revoking removes reading
threads.members(c, viewer, thread_id, *, cursor=None, limit=100) -> {"members": [...], "next_cursor"}
threads.follow(c, viewer, thread_id, *, notify="live") ; threads.unfollow(c, viewer, thread_id)
threads.set_audience(c, principal, thread_id, audience, *, audience_group_id=None)
threads.update(c, principal, thread_id, *, title=None, archived=None)
```
- Posting, being mentioned or owning the thread's task sets `following=1` (reason posted|mentioned|task).
- **Mentions and following never grant access.** A mention outside the audience writes `in_audience=0`, no
  delivery, and returns `warnings: [{"rule": "mention_outside", "actor": ...}]` to the author: on the result of
  `threads.post`, `hubdb.say` and every route built on them (REST answers the dict as is; CLI/MCP print it), beside
  any writing-lint warnings.

### 3.4 Edit and delete (§3.5)

```python
threads.edit_message(c, principal, message_id, body) -> message view   # own `say` only; revision+1
threads.delete_message(c, principal, message_id) -> {"id", "deleted": True, "revision"}
threads.apply_revision(c, origin, *, body=None, deleted=False) -> message view|None   # replicated inbound
```
- Asks, answers and notices can't be edited. Delete clears the body (tombstone: `deleted_at`, body '', revision+1),
  reopens the asks it answered (as `task_comment_delete` does), sets undelivered deliveries to `moot`, re-queues
  outside deliveries at the new revision (an edit or tombstone goes out), and scrubs mentions and caches.
- Highest revision wins; an older or repeated `(origin_id, revision)` is ignored. A delete is final.
- A Tico-held edit is never mirrored out: outside deliveries carry only revisions that were delivered locally.

## 4. C3: delivery

### 4.1 Recipients
Deliveries are written, in the post's transaction, for: the `to_actor`; every in-audience mention, ask or assign;
the other members of a `personal`/`participants` thread (DMs); bots chosen by the wake rule; outside destinations
linked to the thread (`slack:<channel>`, `peer:<install>`, written by those tracks through
`threads.add_outside_delivery`). Never one row per follower: unread counts come from counters (§5).

### 4.2 Modes, reasons and states
- `mode`: `steer` (into the recipient's running turn in the same root), `queue` (start or queue a run; for a person,
  notify live), `defer` (the bot's next run, as `bot_to_bot`/`wake_rules.hold` do today), `digest` (batched), `none`
  (recorded; nothing wakes).
- `reason`: `dm`, `mention`, `ask`, `assign`, `addressed`, `home`, `sub_thread`, `routing`, `digest`, `member`,
  `outside`.
- `state`: `pending` → `delivered` (claimed / shown) → `read`; `capped` (loop guard, resumes); `held` (an org-rule
  hold on the message, local only); `failed` (an outside destination out of retries, one Needs-you item); `moot`
  (deleted before delivery, or the recipient lost access).

### 4.3 Wake rule (in `backend/rules.py`, rule `thread_wake`)
Per (message, bot), deduplicated, first match wins:
1. a DM, or an explicit @mention / ask / assign of the bot → `queue` (`steer` when it is running in that root);
2. the bot's Home → `queue`;
3. a reply in a sub-thread the bot last posted in → `queue`;
4. routing (the Slack gateway's decision model, ≤3 bots) when the message names nobody and the thread has several
   bots → `queue`;
5. a following bot with `notify=digest` → `digest`;
6. otherwise none (no row).

Never wakes: the author itself, imports and replays (`refs.import`/`refs.replay`), a message whose
`refs.origin` names that same bot within the loop window, and follow-up nudges and update cards (`refs.follow_up`,
`refs.card`: `threads.quiet_notice`; `post` also marks them `quiet`, so not even the addressed bot's run starts). Today's bot-to-bot hold (`rules.bot_to_bot_decision`)
and `wake_rules.judge` still run per delivery and may turn `queue` into `defer`.

### 4.4 Runs
The primary bot recipient (`to_actor`) is queued by the existing `queue_bot_message` trigger (job id = message id).
Each further woken bot gets a **carrier**: a keeper notice to that bot in the same thread, `refs={"carries": <id>,
"wake": "thread", "reason": ...}`, no `seq`; its job starts the run, and the delivery's `carrier_id` names it.
`jobs.message_id` stays unique. No carrier is written when the bot may not read the message (audience and the
per-message check); the delivery is `moot`. The per-message check reads a carrier as the message it carries, and a
claim refuses a carrier whose message is gone, moved or no longer readable (`threads.carrier_current`).

Every message write fans out the same way, through `post` or a legacy writer (`hubdb.say`, runner replies, old
routes): mentions, DM members, the Home's owner, sub-thread repliers and outside destinations, under the loop guard.
A legacy write in a `task` thread leaves its mentions to the task rules, and a legacy notice or keeper write wakes no
further bot.

### 4.5 Loop guard
`threads.LOOP_GUARD_PER_HOUR` (default 6, `TICO_THREAD_LOOP_GUARD`): bot-caused wakes (`cause='bot'`, bot
recipient, `steer|queue`) per `root_id` per rolling hour. Past it a wake is written `state='capped'` (never
dropped) and the root gets one Needs-you item (`threads.capped(c, viewer)`, deduplicated per root). Capped
deliveries resume in order (`threads.resume_capped(c, root_id)`) when a human posts in the root or the window
reopens (the scheduler calls `threads.reopen_windows(c)`).

### 4.6 Readers rekeyed to deliveries
The inbox (`H.inbox_pages`, `H.inbox`, `H.undelivered`), `H.mark_delivered`/`mark_read`, `_reach` and the
unsolicited cap (per delivery), task-comment target resolution, `rules.judge` and `wake_rules.gate`, and the runner
claim path read `message_deliveries`. `to_actor` keeps meaning "addressed to" for the readers that ask exactly
that (open asks to a person, approvals).

### 4.7 Solicited vs unsolicited (C2)
In a DM the person opened, every bot post is solicited; posts carrying a task or approval ref stay solicited. In a
person's Home or an unaddressed post, a bot counts as solicited only when it answers that person (a mention, ask
or reply to them). Otherwise today's cap (`UNSOLICITED_PER_DAY`) applies, counted per delivery.

## 5. C4: order and read state
- `messages.seq` is per install, strictly increasing, from `sequences('messages')`. Tree order:
  `parent_message_id` + `seq`. `created` stays for display.
- Counters per thread: `last_seq` (every message), `last_unread_seq` (a human's message, or one addressed to
  someone: `to_actor` set or any mention). Sidebar aggregates per root come from the counters of the root's subtree,
  never from per-follower writes.
- A timeline is one tree: `threads.timeline` pages the opened thread's own messages, and each is followed by its
  sub-thread's messages, recursively, each with its `depth`. A long branch shows its latest `BRANCH_TAIL` (20) under one
  `collapsed` row (§10); `GET /threads/{that thread}/timeline?cursor=<the row's cursor>` reads the rest. Every row passes
  the per-message check. Past `MAX_INLINE_ROWS`, `MAX_BRANCH_LEVELS` or the byte cap, a branch is its summary and a
  `collapsed` row. A hidden message's branch does not show. The queries follow the tree's depth, not its branches.
- A row's `sub_thread.title` is a title someone gave that branch: `""` while the stored one is only its parent's first
  line, then or before an edit. The opened thread's own `title` is always the stored one.
- Read state: `thread_followers.last_read_seq` per (thread, reader); max wins (devices); it advances only for
  threads displayed, up to the last rendered message. A child never clears its siblings. The mark is one number, so a
  branch whose `collapsed` row says `unread` is not marked until that part is opened.

```python
threads.mark_read(c, viewer, thread_id, seq) -> {"thread_id", "last_read_seq"}
threads.mark_all_read(c, viewer, thread_id) -> {"threads": n}        # the whole subtree
threads.unread(c, viewer, thread_id) -> {"unread": n, "activity": bool}
```
- Join: read up to now. Deletes and backfill (imports, migration, replays) are never unread. Losing access: the
  follower row is dropped on the next read.
- Unread count for a viewer = messages in the thread with `seq > last_read_seq` that are human-authored or
  addressed to that viewer (a delivery row for them), excluding their own. Other bot posts are **activity** (a
  dot, never a count).

## 6. C5: replication
- `origin_id` = `'<uuidv7>@<install id>'` (`census.install_id_in`), immutable; replicated messages keep the
  sender's. `revision` starts at 0 and +1 per edit or delete.
- `message_revisions` holds every `(origin_id, revision)` seen, durable for the life of the message.
- Local holds and screening change only local delivery state, never the canonical message.

## 7. Migration (same release, `threads.migrate(c)`, once)
Existing conversations (and conversations any legacy writer inserts later) keep `audience` NULL. **Who reads a root
with no stored audience is decided by the pre-threads rules, exactly** (`threads._legacy_denial`, and the legacy term
of `threads.readable_sql`): a task room by the task; a person by `scope` (personal: its owner, the Strategy room
owner-only; shared: `rooms.shared_member_ids` plus Read on the bot, live; otherwise the owner or a participant), a
bound meeting room by the meeting; an agent bot by participation or `rooms.room_bot`; a run by its attempt's grant.
The only narrowing: a person who left, or whose sign-in is off, reads nothing. The e2e reader-set goldens
(`backend/tests/fixtures/threads/reader-sets-*.json`) pin it. The audience *label* is still derived live from
`room_key`, `scope`, `kind` and `task_id` (`threads.audience_of`) for sessions, wakes and posting rules,
because legacy writers set those columns after the insert. A sub-thread of such a root reads as its root. Likewise `owner_actor` stays as written.
Threads made by `threads.create`/`home`/`sub_thread` store an audience, and so does each bot's open shared room: the
migration stores `audience='home'`, `owner_actor='bot:<slug>'` on it. Archived duplicate shared rooms, and a shared
room `rooms.sync_shared_room` opens after the migration (a bot newly in shared mode), stay unstored and read by the
live shared-room rule as before.

| today | audience | notes |
|---|---|---|
| `scope='personal'` (Assistant, Librarian, Strategy, legacy archives) | `personal` | `room_key` kept |
| `scope='task'` / `kind='task'` | `task` | |
| `scope='direct'` (any mix of humans and bots) | `participants` | |
| `room_key` starting `live_privacy.ROOM_PREFIX` | `meeting` | |
| `scope='shared'` | `home`, `owner_actor='bot:<slug>'` | every existing message gets `legacy_audience='shared:<slug>'`, `restricted=1` |

- Every row: `root_id=id`, `path='/<id>/'`, `parent_id` NULL; messages: `seq=rowid`, `origin_id` minted,
  `revision=0`, `restricted` computed. `sequences('messages')` starts at `MAX(rowid)`.
- Counters start at the migrated `MAX(seq)` and every existing reader's `last_read_seq` = that (backfill is never
  unread).
- Homes for bots without a shared room, people and groups are created lazily (`threads.home`).
- Managers of a bot with legacy history: `threads.open_earlier_messages(c, principal, slug)` clears
  `legacy_audience` on that Home's messages. The 15-minute follow-ups job (`thread_followups.earlier_sweep`) tells each
  manager once, in their DM with the bot (`refs.earlier_messages.thread_id`, never a wake), since the Home's readers
  must not see it; the Home's menu and its Earlier messages row carry the same control.
- The reader-set equality fixture asserts, per message, the same readers before and after, and again after a
  roster change.

## 8. Sessions (`clients/sessions.py`, `runner-rs/src/execute.rs`)
The server adds `root_id` and `audience_class` (`public` | `private`) to the turn's `conversation`. `private` =
audience `members|personal|participants|meeting`, a private task's thread, or a legacy-audience message as the input.

| mode | task turn | non-task turn, public | non-task turn, private |
|---|---|---|---|
| `session: bot` | `bot` thread; a private task (`task.private`), or a task turn in a private thread, `task:<id>` (stateless bots always `task:<id>`) | `public` | `root:<root_id>:private` |
| default | `task:<id>` | `root:<root_id>:public` | `root:<root_id>:private` |
| `session: task` | `task:<id>` | `root:<root_id>:public` | `root:<root_id>:private` |

- A turn from an older server (no `root_id`) keys as before, except that a private task on `session: bot` still
  gets `task:<id>`.
- Existing mappings: a `conversation:<id>` session is adopted as `root:<id>:<class>` on first use (the runner
  copies it); `task:<id>` stays. The old shared `bot` thread mixed private and public context, so it is **not**
  carried into `public` (a deviation recorded in the report).
- So chat turns start fresh once at rollout: the first turn under `public` or a `root:` key, with nothing to adopt and
  no Clear, is given its conversation's latest messages (`fallback::first_turn_pack`, the fallback's replay and size
  limit). On a public turn the server flags what some reader of the thread may not read (`audience_limited`:
  `threads.mark_narrower`), and the runner never gives a public session those. Task turns on `bot` keep their session.
- Lanes: `task:` keys run in their own lane; `root:` and `public` share the main lane. A private task on
  `session: bot` keeps the main lane (the bot's one checkout); only its session is its own.

## 9. Events (SSE, `GET /api/v2/events`)

`messages` topic, one event per changed message the viewer may read:
```json
{"seq": 812, "actor": "human:ana", "at": "...", "id": "<message id>", "conversation_id": "<thread id>",
 "thread_id": "<thread id>", "root_id": "<root id>", "message_seq": 4411, "revision": 0,
 "message": {"id": "...", "conversation_id": "...", "thread_id": "...", "root_id": "...", "seq": 4411,
             "revision": 0, "from_actor": "...", "to_actor": null, "kind": "say", "body": "...", "refs": {},
             "created": "...", "edited_at": null}}
{"seq": 813, ..., "id": "<message id>", "thread_id": "...", "root_id": "...", "deleted": true, "revision": 2}
```
`threads` topic (`changes.topic='threads'`, `subject_id` = thread id, `conversation_id` = thread id; its view is
`_threads` in `backend/events.py`):
```json
{"seq": 900, "thread_id": "...", "root_id": "...",
 "kind": "created|renamed|archived|unarchived|audience|members|read|sub_thread|counters|reactions",
 "thread": {<thread view>},                      // created|renamed|archived|unarchived|audience|members|sub_thread
 "read": {"last_read_seq": 4411},                // kind=read, only to that reader
 "counters": {"last_seq": 4411, "last_unread_seq": 4400},   // kind=counters
 "message_id": "...", "reactions": [{"emoji": ":+1:", "count": 2, "actors": ["human:ana", "bot:legal"]}]}
                                                 // kind=reactions, only when the viewer reads that message
```
`changes.kind` values written by core's triggers: `created`, `renamed`, `archived`, `unarchived`, `audience`,
`members`, `sub_thread`, `counters` (on `conversations`), `read` (on `thread_followers`, `ref` = actor),
`reactions` (on `message_reactions`, `ref` = message id).

`counters` does **not** fire per message. A new message moves its thread's counters, and the `messages` event already
carries them (`message_seq`, and the thread's `last_seq` is the highest `message_seq` seen): clients derive unread
counts and badges from `messages` events plus their own `read` events. `counters` is written only when the counters
change without a message (the threads migration, a repair), and tells clients to replace their numbers.

## 10. REST shapes (§3.4; T2 builds the routes on these service functions)

Thread view (`threads.view(c, viewer, row)`):
```json
{"id": "...", "title": "...", "audience": "members", "audience_group_id": null, "owner_actor": "human:ana",
 "parent_id": null, "parent_message_id": null, "root_id": "...", "path": ["<root>", "..."], "kind": "chat",
 "task_id": null, "created_at": "...", "last_message_at": "...", "archived_at": null,
 "last_seq": 4411, "last_unread_seq": 4400,
 "home": {"actor": "bot:legal"},                 // audience=home only
 "me": {"member": true, "following": true, "notify": "live", "last_read_seq": 4400, "unread": 3,
        "activity": true, "can_post": true, "can_manage": true}}
```

| Route | Service function |
|---|---|
| `GET /api/v2/threads?cursor=&limit=` | `threads.sidebar(c, viewer, cursor=None, limit=50)` → `{"attention": [...], "threads": [...], "dms": [...], "next_cursor"}`; `attention`: threads with `me.unread` (mentions, asks, DM messages), then every root the viewer owns, is in or follows that holds capped wakes, each with `"capped": n, "capped_since"` |
| `GET /api/v2/threads/{id}` | `threads.get(c, viewer, thread_id)` → thread view + `"breadcrumb": [{"id","title"}]` |
| `GET /api/v2/threads/{id}/timeline?cursor=&limit=` | `threads.timeline(c, viewer, thread_id, cursor=None, limit=50, max_bytes=...)` → `{"thread", "breadcrumb", "messages": [...], "next_cursor"}`; `next_cursor` and `limit` count the thread's own messages; each is followed by its sub-thread's rows in tree order (§5). Each message carries `depth` (its thread's depth in the tree: 0 in a root, 1 in a sub-thread), and a parent carries `"sub_thread": {"id", "title", "count", "last_seq", "last_read_seq", "unread", "more"}` (`count` and `unread` only of messages the viewer reads; `more` is how many are collapsed) |
| `POST /api/v2/threads/{id}/read {seq}` | `threads.mark_read` |
| `POST /api/v2/threads/{id}/read-all` | `threads.mark_all_read` |
| `GET /api/v2/threads/search?q=` | T2 (FTS5), filtered by `threads.readable` per message |
| `POST /api/v2/threads {title, audience, audience_group_id, members, parent_message_id, body}` | `threads.create` (+ `threads.post` when `body`) |
| `POST /api/v2/threads/{id}/messages {text, kind, to, refs, parent_message_id}` | `threads.post` |
| `PATCH /api/v2/threads/{id} {title, archived, audience, audience_group_id}` | `threads.update` / `threads.set_audience` |
| `GET/POST/DELETE /api/v2/threads/{id}/members[/{actor}]` | `threads.members` / `add_member` / `remove_member` |
| `PUT/DELETE /api/v2/threads/{id}/follow {notify}` | `threads.follow` / `unfollow` |
| `PATCH/DELETE /api/v2/messages/{id}` | `threads.edit_message` / `delete_message` |
| `PUT/DELETE /api/v2/messages/{id}/reactions/{emoji}` | `threads.react(c, principal, message_id, emoji)` / `threads.unreact(...)` → `[{"emoji", "count", "actors"}]`; 404 when the message is unreadable, 422 for a bad emoji or past 50 distinct |

Timeline messages: the existing message view plus `thread_id`, `root_id`, `seq`, `revision`, `origin_id`,
`edited_at`, `deleted` (tombstone: no body), `reactions` (`threads.reactions(c, ids)`, when any), hidden rows as `{"id", "seq", "thread_id", "depth", "hidden": true}` (with `"earlier": true` for closed history, §2.3), and a long branch's older part as
`{"id": "more:<thread id>", "thread_id", "depth", "collapsed": true, "count", "cursor", "unread"}`.
`/api/v2/conversations/*` stay as aliases for one release. Every list is paged and byte-capped.

## 11. Outside tracks' hooks
- Thread webhooks: `threads._emit_thread_created(c, thread)` (create and sub_thread),
  `threads._emit_message_created(c, thread, message)` (post) and `threads._emit_message_held(c, thread, actor,
  verdict)` (a held post) are no-ops on core; integration points them at `backend/thread_webhooks.py`. They run inside
  the write transaction and must only enqueue.
- Runner replies in a sub-thread: the claim's `message` carries `parent_message_id` (its thread's), the runner sends
  it back as `Completion.parent_message_id`, and `threads.reply_destination(c, actor, thread, parent_message_id)`
  puts the reply in that message's sub-thread when it is in the run's thread tree (else the run's thread).
- Slack/federation inbound: `threads.post(..., via="slack"|"peer", origin={...})`; edits and deletes:
  `threads.apply_revision`.
- Remote authors (spec §7.2): `threads.post(c, "keeper", thread_id, body, via="peer", origin={..., "install": I},
  as_actor="human:ana@I")`. Only the keeper principal with `via="peer"` may pass `as_actor` (else 403); it must match
  `^(human|bot):[^@]+@<origin.install>$` and not be a local teammate (else 422); kind say|ask|answer. It is stored
  as `from_actor`. Remote actors are guests: C1 for them is a `thread_members` row on the root for the actor or for
  `peer:<install>` (else 404, and `audience_has` is false); never an owner (`add_member(..., role="owner")` → 422),
  never Manage, no bot in-turn grant, no follower rows. `threads.remote_guest(c, actor)` → the install or None.
  Federation may call with the keyword and fall back without it.
- `threads.remove_outside_member(c, principal, thread_id, actor) -> {"thread_id", "recipient", "removed"}` takes a
  `slack:`/`peer:` destination off a thread (Manage on the thread or an admin; an actor-string principal must be
  `keeper`); its pending deliveries become `moot`; audit row and `thread.outside_removed` event. Bridges call this,
  never a direct `thread_members` delete.
- Outbound: `threads.add_outside_delivery(c, thread_id, recipient)` registers a linked destination; core writes a
  delivery per message (and per revision) for it; the track's sender reads
  `threads.outside_due(c, prefix, limit)` and settles with `threads.settle_delivery(c, delivery_id, state, error=None)`.
- Meet: transcripts post with `refs.live_meeting` (restricted).
- Needs attention (T7): `threads.capped(c, viewer)`, `threads.unread_dms(c, viewer)`, plus deliveries with
  `mode` in `ask`/`mention` reasons and `state` `pending|delivered`.
