"""Project source-bearing fields of the task/source APIs and runner wire schema.

Text is opaque. The runner's refs_json follows structured message.refs when those refs change.
Task history provenance comes from its stored data column, addressed by event id.
Unknown references survive; only a resolved source that fails containment is removed.
"""
import re
from urllib.parse import unquote

from .store import H, Problem

# These fields belong to source/reference schemas, never prose or arbitrary JSON strings.
REF_FIELDS = frozenset({"file", "file_id", "page", "page_id", "blob_id", "meeting", "meeting_id",
                        "task", "task_id", "message", "message_id", "comment", "comment_id",
                        "digest", "source_digest", "sha256", "content_sha256", "body_sha256",
                        "ref", "item", "url", "file_url", "href", "src", "path"})
SOURCE_LISTS = frozenset({"files", "attachments", "pages", "page_files", "meetings", "reviewed", "citations",
                          *H.SOURCED_TEXT_SOURCES})
SOURCE_ID = re.compile(r"(?:file-[0-9a-f]{24}|[0-9a-f]{64}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}|"
                       r"(?:file|page|task|message|meeting):[A-Za-z0-9_-]+)(?:@[0-9]+)?")


def object_refs(value):
    """Structured identities belong to their object, regardless of its enclosing field's name."""
    return ((key, item) for key, item in value.items() if key in REF_FIELDS or key == "id" or
            key not in H.SOURCED_TEXT_FIELDS and isinstance(item, str) and SOURCE_ID.fullmatch(item))


def body_sources(value, parent=""):
    """Keep structured source refs beside generated prose, without duplicating the payload's ordinary text."""
    if isinstance(value, dict):
        out = {}
        identities = dict(object_refs(value))
        for key, item in value.items():
            if key in identities:
                out[key] = item
            elif isinstance(item, (dict, list)):
                refs = body_sources(item, key)
                if refs:
                    out[key] = refs
        return out
    if isinstance(value, list):
        return [refs for item in value if (refs := body_sources(item, parent))]
    return value if parent in SOURCE_LISTS and isinstance(value, str) else None


def project_notes(c, scoped, payload, auth):
    """Deferred notes are rebuilt through their stored message, before their cached prose is delivered."""
    from . import turn_scope
    def notes(value):
        if isinstance(value, dict):
            value = {key: notes(item) for key, item in value.items()}
            if value.get("message_id") and {"body", "from_actor", "to_actor"}.issubset(value):
                source = H.message(c, value["message_id"])
                shown = turn_scope.message_view(c, source, who=scoped.who, auth=auth)
                value["body"], value["refs"] = H.sourced_text(
                    value["body"] if source and shown and source.get("body") == shown.get("body") else "",
                    [body_sources((source or {}).get("refs") or {})], {"message": value["message_id"]})
            return value
        return [notes(item) for item in value] if isinstance(value, (list, tuple)) else value

    return notes(payload)


def project(c, scoped, payload, *, auth=None):
    from . import turn_scope, context_search
    from .files import file_visible, prime_visible
    if scoped is None:
        return payload
    auth = auth or turn_scope.AUTH.get()
    if auth is None or scoped.who is None:
        raise Problem("privacy", "Source projection requires a scoped principal", 403)
    payload = project_notes(c, scoped, payload, auth)

    def reference(value):
        if not isinstance(value, str):
            return None
        value = unquote(value)
        # Conversation and opaque origin links keep their existing namespace rules.
        if value.startswith("conversation:") or "/origins/" in value:
            return None
        match = re.search(r"(?:page:|file:|meeting:|task:|message:|/pages?/|/files/|/meetings/|/tasks/|/messages/|[?&]page=)([A-Za-z0-9_-]+)", value)
        return match[1] if match else value.rsplit("@", 1)[0]

    names, task_ids, event_ids, event_sources = set(), set(scoped.tasks), set(), {}

    def collect(value, parent=""):
        if isinstance(value, dict):
            if "owner_id" in value and "title" in value and isinstance(value.get("id"), str):
                task_ids.add(value["id"])
            if isinstance(value.get("task"), str):
                task_ids.add(value["task"])
            if isinstance(value.get("task_id"), str):
                task_ids.add(value["task_id"])
                if value.get("field") == "file" and isinstance(value.get("id"), str):
                    event_ids.add(value["id"])
            for _, item in object_refs(value):
                ref = reference(item)
                if ref:
                    names.add(ref)
            for key, item in value.items():
                if key != "refs_json":
                    collect(item, key)
        elif isinstance(value, (list, tuple)):
            for item in value:
                if parent in SOURCE_LISTS and isinstance(item, str):
                    ref = reference(item)
                    if ref:
                        names.add(ref)
                collect(item, parent)
    collect(payload)
    stored_text, checked_text = {}, set()
    while pending := (names | task_ids) - checked_text:
        checked_text.update(pending)
        for chunk in H.chunks(sorted(pending)):
            marks = ','.join('?' * len(chunk))
            for row in c.execute(f"SELECT record_id,field,refs FROM sourced_text_records WHERE kind='task' "
                                 f"AND record_id IN ({marks})", chunk):
                refs = H._json(row['refs'], {})
                stored_text.setdefault(row['record_id'], {})[row['field']] = refs
                collect(refs)
    # Task history omits its stored data column. Resolve only those event ids, never parse display text.
    for chunk in H.chunks(sorted(event_ids)):
        marks = ','.join('?' * len(chunk))
        for row in c.execute(f"SELECT id,data FROM history_events WHERE id IN ({marks}) AND kind='file'", chunk):
            data = H._json(row['data'], {})
            source = data.get('body_sources') or data.get('source') or body_sources(data)
            if source:
                event_sources[row['id']] = source
                collect(source)
    # Legacy choices lost their provenance. Limit the title fallback to sources attached to these tasks.
    legacy_sources = []
    for chunk in H.chunks(sorted(task_ids)):
        marks = ','.join('?' * len(chunk))
        legacy_sources.extend(c.execute(f"SELECT id,title,task_id,scope FROM bot_files WHERE task_id IN ({marks}) OR scope IN ({marks})",
                                         (*chunk, *("task:" + tid for tid in chunk))))
        legacy_sources.extend(c.execute(f"SELECT f.id,f.title,substr(p.place,6) AS task_id,'' AS scope FROM pins p "
                                         f"JOIN bot_files f ON p.item='page:' || f.id WHERE p.place IN ({marks})",
                                         tuple("task:" + tid for tid in chunk)))
        legacy_sources.extend(c.execute(f"SELECT f.id,v.name AS title,f.task_id,f.scope FROM bot_files f JOIN bot_file_versions v ON v.file_id=f.id "
                                         f"WHERE f.task_id IN ({marks})", chunk))
        legacy_sources.extend(c.execute(f"SELECT b.id,b.name AS title,a.task_id,'' AS scope FROM blobs b JOIN task_assets a ON a.blob_id=b.id "
                                         f"WHERE a.task_id IN ({marks})", chunk))
        legacy_sources.extend(c.execute(f"SELECT m.id,m.title,l.task_id,'' AS scope FROM meetings m JOIN meeting_task_links l ON l.meeting_id=m.id "
                                         f"WHERE l.task_id IN ({marks})", chunk))
    delivered_names = set(names)
    names.update(row['id'] for row in legacy_sources)

    def checking(ref, fn):
        # The legacy fallback checks titles without delivering their source metadata.
        reset = turn_scope.READS.set(None) if ref not in delivered_names else None
        try:
            return fn()
        finally:
            if reset is not None:
                turn_scope.READS.reset(reset)

    files, blobs, meetings, live, versions, tasks, messages = {}, {}, set(), {}, [], {}, {}
    for chunk in H.chunks(sorted(names)):
        marks = ','.join('?' * len(chunk))
        files.update((r['id'], r) for r in c.execute(f"SELECT * FROM bot_files WHERE id IN ({marks})", chunk))
        blobs.update((r['id'], r) for r in c.execute(f"SELECT id,digest FROM blobs WHERE id IN ({marks}) OR digest IN ({marks})", (*chunk, *chunk)))
        meetings.update(r[0] for r in c.execute(f"SELECT id FROM meetings WHERE id IN ({marks})", chunk))
        live.update(c.execute(f"SELECT id,imported_meeting_id FROM live_meetings WHERE id IN ({marks})", chunk))
        tasks.update((r['id'], r) for r in c.execute(f"SELECT * FROM tasks WHERE id IN ({marks})", chunk))
        messages.update(H.prime_messages(c, chunk))
        versions.extend(c.execute(f"SELECT file_id,digest,source_digest FROM bot_file_versions WHERE digest IN ({marks}) "
                                  f"OR source_digest IN ({marks})", (*chunk, *chunk)))
    for chunk in H.chunks(sorted({v['file_id'] for v in versions} - files.keys())):
        marks = ','.join('?' * len(chunk))
        files.update((r['id'], r) for r in c.execute(f"SELECT * FROM bot_files WHERE id IN ({marks})", chunk))
    prime_visible(c, list(files.values()))
    visible = {fid: not row['archived'] and checking(fid, lambda row=row: file_visible(c, auth, scoped.who, row, {}))
               for fid, row in files.items()}
    denied = {fid for fid, ok in visible.items() if not ok}
    from . import task_privacy as privacy
    content_denied = set()
    # Message access walks stored reply ancestry, whose decorators also call the projector. Read that ancestry
    # without projecting it recursively; this pass still projects every object in the outgoing payload.
    reset = turn_scope.WHO.set(None)
    try:
        for tid, row in tasks.items():
            try:
                auth.task_row(c, scoped.who, dict(row))
            except Problem as exc:
                if exc.status not in (403, 404):
                    raise
                content_denied.add(tid)
        for mid, row in messages.items():
            try:
                # The message read route admits a bot's addressed legacy messages outside its own room.
                native = bool((H.conversation(c, row['conversation_id']) or {}).get('audience'))
                party = scoped.who.actor in (row['sender_id'], row['recipient_id'])
                if not (scoped.who.role == 'bot' and party and not native):
                    auth.conversation(c, scoped.who, row['conversation_id'])
                if not privacy.message_readable(c, privacy.actor(scoped.who), row, current=True):
                    content_denied.add(mid)
            except Problem as exc:
                if exc.status not in (403, 404):
                    raise
                content_denied.add(mid)
    finally:
        turn_scope.WHO.reset(reset)
    denied.update(content_denied)
    digest_access = {}
    for bid, row in blobs.items():
        ok = checking(bid, lambda bid=bid: turn_scope.blob_allowed(c, auth, scoped.who, bid)) is True
        if not ok:
            denied.add(bid)
        digest_access[row['digest']] = digest_access.get(row['digest'], False) or ok
    for row in versions:
        for key in ('digest', 'source_digest'):
            digest = row[key]
            if digest:
                digest_access[digest] = digest_access.get(digest, False) or visible.get(row['file_id'], False)
    denied.update(digest for digest, ok in digest_access.items() if not ok)
    meetings.update(mid for mid in live.values() if mid)
    for mid in meetings:
        try:
            checking(mid, lambda mid=mid: context_search.readable(c, scoped.who, mid, auth=auth))
        except Problem as exc:
            if exc.status not in (403, 404):
                raise
            denied.add(mid)
    from .live_meetings import _require_read
    for rid, mid in live.items():
        try:
            _require_read(c, auth, scoped.who, rid)
            if not mid or mid in denied:
                denied.add(rid)
        except Problem as exc:
            if exc.status not in (403, 404):
                raise
            denied.add(rid)
    titles_by_task, titles_by_source = {}, {}
    for row in legacy_sources:
        if row['id'] not in denied or not row['title']:
            continue
        titles_by_source.setdefault(row['id'], set()).add(row['title'])
        for tid in (row['task_id'], row['scope'][5:] if row['scope'].startswith('task:') else None):
            if tid:
                titles_by_task.setdefault(tid, set()).add(row['title'])

    for fid, row in files.items():
        if fid in denied and row['title']:
            titles_by_source.setdefault(fid, set()).add(row['title'])

    def task_titles(ids):
        return set().union(*(titles_by_task.get(tid, set()) for tid in ids))

    def attached_titles(value, parent=''):
        found = set()
        if isinstance(value, dict):
            for _, item in object_refs(value):
                found.update(titles_by_source.get(reference(item), set()))
            for key, item in value.items():
                found.update(attached_titles(item, key))
        elif isinstance(value, list):
            for item in value:
                if parent in SOURCE_LISTS and isinstance(item, str):
                    found.update(titles_by_source.get(reference(item), set()))
                found.update(attached_titles(item, parent))
        return found
    # A postal item's file id aliases the exact scan addressed by file_url, not other versions of that file.
    def scan_alias(value):
        if not {'external_id', 'address_id', 'file_url'}.issubset(value):
            return False
        bid = reference(value.get('file_url'))
        return bool(bid in blobs and bid not in denied and c.execute(
            "SELECT 1 FROM bot_file_versions WHERE file_id=? AND blob_id=?",
            (value.get('file_id'), bid)).fetchone())

    gone = object()

    def rejected(value, seen=frozenset()):
        ref = reference(value)
        return ref in denied or ref in stored_text and ref not in seen and has_denied(stored_text[ref], seen | {ref})

    def has_denied(value, seen=frozenset()):
        if isinstance(value, str):
            return rejected(value, seen)
        if isinstance(value, list):
            return any(has_denied(item, seen) for item in value)
        if isinstance(value, dict):
            return (any(has_denied(item, seen) for _, item in object_refs(value)) or
                    any(has_denied(item, seen) for item in value.values() if isinstance(item, (dict, list))))
        return False

    def walk(value, parent='', legacy_titles=None):
        legacy_titles = task_titles(scoped.tasks) if legacy_titles is None else legacy_titles
        if isinstance(value, dict):
            # A same-spelled label on another task has no relationship to this task's rejected source.
            refs = value.get('refs') if isinstance(value.get('refs'), dict) else {}
            tid = refs.get('task') or value.get('task_id')
            if 'owner_id' in value and 'title' in value:
                tid = value.get('id')
            if isinstance(value.get('task'), dict):
                tid = value['task'].get('id')
            if isinstance(tid, str):
                legacy_titles = task_titles((tid,))
            if refs or isinstance(value.get('answers'), dict):
                legacy_titles = legacy_titles | attached_titles(refs or value)

            if value.get('field') == 'file' and (has_denied(event_sources.get(value.get('id'))) or
                    value.get('id') not in event_sources and any(value.get(k) in legacy_titles for k in ('old', 'new'))):
                return gone
            alias = scan_alias(value)
            direct_denied = {reference(item) for key, item in object_refs(value)
                             if not (alias and key == 'file_id') and rejected(item)}
            if direct_denied - content_denied - stored_text.keys():
                return gone
            if has_denied(value.get('source')) or has_denied(value.get('ref')):
                return gone
            if parent == 'options' and not value.get('file') and not value.get('source') and value.get('label') in legacy_titles:
                return gone
            out = {}
            for key, item in value.items():
                if key == 'refs_json':
                    continue
                shown = walk(item, key, legacy_titles)
                if shown is not gone:
                    out[key] = shown
            # The answer map holds selected labels as strings, with their provenance next to the map.
            if isinstance(value.get('answers'), dict):
                sources = value.get('label_sources', {})
                sources = sources if isinstance(sources, dict) else {}
                for key in ('answers', 'not'):
                    if isinstance(value.get(key), dict) and isinstance(out.get(key), dict):
                        for qid, picks in value[key].items():
                            if qid not in out[key] or not isinstance(picks, list) or not all(isinstance(pick, str) for pick in picks):
                                continue
                            labels = sources.get(qid, {})
                            labels = labels if isinstance(labels, dict) else {}
                            out[key][qid] = [label for label in picks if not (
                                has_denied(labels.get(label)) or label not in labels and label in legacy_titles)]
            H.clear_sourced_text(value, out, has_denied, hidden=bool(direct_denied))
            dependency = value.get('blocked_kind') == 'dependency' or any(
                hold.get('kind') == 'blocked' and hold.get('cause') == 'dependency' for hold in value.get('holds') or [])
            record_id = value.get('id') if isinstance(value.get('id'), str) else None
            if dependency and record_id in tasks:
                from . import task_holds
                row = H.task(c, record_id)
                reason = task_holds.words(c, task_holds.holds(c, row, who=scoped.who, auth=auth))
                if 'blocked_reason' in out:
                    out['blocked_reason'] = reason
                for hold in out.get('holds') or []:
                    if hold.get('kind') == 'blocked' and hold.get('cause') == 'dependency':
                        hold['reason'] = reason
            for field, provenance in stored_text.get(record_id, {}).items():
                if field in out:
                    copied = {field: out[field], **provenance}
                    out[field] = H.clear_sourced_text(copied, dict(copied), has_denied)[field]
                if field == 'blocked_reason':
                    for hold in out.get('holds') or []:
                        if hold.get('kind') == 'blocked':
                            copied = {**hold, **provenance}
                            hold.update(H.clear_sourced_text(copied, dict(copied), has_denied))
            refs = value.get('refs') if isinstance(value.get('refs'), dict) else {}
            shown_refs = out.get('refs') if isinstance(out.get('refs'), dict) else {}
            if 'refs_json' in value:
                # Keep the original bytes when the named wire copy does not change.
                if shown_refs == refs:
                    out['refs_json'] = value['refs_json']
                else:
                    out['refs_json'] = H._dump(shown_refs)
            return out
        if isinstance(value, (list, tuple)):
            out = []
            for item in value:
                if parent in SOURCE_LISTS and isinstance(item, str) and rejected(item):
                    continue
                shown = walk(item, parent, legacy_titles)
                if shown is not gone:
                    out.append(shown)
            return out
        return value
    result = walk(payload)
    return None if result is gone else result
