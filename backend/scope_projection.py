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
                        "digest", "source_digest", "ref", "item", "url", "file_url", "href", "src", "path"})
SOURCE_LISTS = frozenset({"files", "attachments", "pages", "meetings", "reviewed", "citations", "body_sources"})


def project(c, scoped, payload, *, auth=None):
    from . import turn_scope, context_search
    from .files import file_visible, prime_visible
    if scoped is None:
        return payload
    auth = auth or turn_scope.AUTH.get()
    if auth is None or scoped.who is None:
        raise Problem("privacy", "Source projection requires a scoped principal", 403)

    def reference(value):
        if not isinstance(value, str):
            return None
        value = unquote(value)
        # Task/conversation refs belong to another namespace, including /origins/<task>.
        if value.startswith(("task:", "conversation:")) or "/origins/" in value:
            return None
        match = re.search(r"(?:page:|file:|meeting:|/pages?/|/files/|/meetings/|[?&]page=)([A-Za-z0-9_-]+)", value)
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
            for key, item in value.items():
                if key in REF_FIELDS or key == "id" and parent in SOURCE_LISTS:
                    ref = reference(item)
                    if ref:
                        names.add(ref)
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
    # Task history omits its stored data column. Resolve only those event ids, never parse display text.
    for chunk in H.chunks(sorted(event_ids)):
        marks = ','.join('?' * len(chunk))
        for row in c.execute(f"SELECT id,data FROM history_events WHERE id IN ({marks}) AND kind='file'", chunk):
            source = H._json(row['data'], {}).get('source')
            if source:
                event_sources[row['id']] = source
                collect(source)
    # Legacy choices lost their provenance. Limit the title fallback to sources attached to these tasks.
    legacy_sources = []
    for chunk in H.chunks(sorted(task_ids)):
        marks = ','.join('?' * len(chunk))
        legacy_sources.extend(c.execute(f"SELECT id,title,task_id,scope FROM bot_files WHERE task_id IN ({marks}) OR scope IN ({marks})",
                                         (*chunk, *("task:" + tid for tid in chunk))))
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

    files, blobs, meetings, live, versions = {}, {}, set(), {}, []
    for chunk in H.chunks(sorted(names)):
        marks = ','.join('?' * len(chunk))
        files.update((r['id'], r) for r in c.execute(f"SELECT * FROM bot_files WHERE id IN ({marks})", chunk))
        blobs.update((r['id'], r) for r in c.execute(f"SELECT id,digest FROM blobs WHERE id IN ({marks}) OR digest IN ({marks})", (*chunk, *chunk)))
        meetings.update(r[0] for r in c.execute(f"SELECT id FROM meetings WHERE id IN ({marks})", chunk))
        live.update(c.execute(f"SELECT id,imported_meeting_id FROM live_meetings WHERE id IN ({marks})", chunk))
        versions.extend(c.execute(f"SELECT file_id,digest,source_digest FROM bot_file_versions WHERE digest IN ({marks}) "
                                  f"OR source_digest IN ({marks})", (*chunk, *chunk)))
    for chunk in H.chunks(sorted({v['file_id'] for v in versions} - files.keys())):
        marks = ','.join('?' * len(chunk))
        files.update((r['id'], r) for r in c.execute(f"SELECT * FROM bot_files WHERE id IN ({marks})", chunk))
    prime_visible(c, list(files.values()))
    visible = {fid: not row['archived'] and checking(fid, lambda row=row: file_visible(c, auth, scoped.who, row, {}))
               for fid, row in files.items()}
    denied = {fid for fid, ok in visible.items() if not ok}
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
            for key, item in value.items():
                if key in REF_FIELDS or key == 'id' and parent in SOURCE_LISTS:
                    found.update(titles_by_source.get(reference(item), set()))
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

    def rejected(value):
        return reference(value) in denied

    def has_denied(value):
        if isinstance(value, str):
            return rejected(value)
        if isinstance(value, list):
            return any(has_denied(item) for item in value)
        if isinstance(value, dict):
            return any(has_denied(item) for key, item in value.items() if key in REF_FIELDS or key == 'id')
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
            if any(rejected(item) for key, item in value.items() if key in REF_FIELDS and not (alias and key == 'file_id')):
                return gone
            if parent in SOURCE_LISTS and rejected(value.get('id')):
                return gone
            if has_denied(value.get('source')):
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
                for key in ('answers', 'not'):
                    if isinstance(value.get(key), dict):
                        out[key] = {qid: [label for label in picks if not (
                            has_denied(sources.get(qid, {}).get(label)) or
                            label not in sources.get(qid, {}) and label in legacy_titles)]
                                    for qid, picks in value[key].items()}
            # Generated prose is removed only on its stored provenance or a rejected legacy answer label.
            refs = value.get('refs') if isinstance(value.get('refs'), dict) else {}
            shown_refs = out.get('refs', {})
            answer, shown_answer = refs.get('answer', {}), shown_refs.get('answer', {})
            hidden_body = (has_denied(refs.get('body_sources', [])) or has_denied(answer.get('target')) or
                           answer.get('answers') != shown_answer.get('answers'))
            if hidden_body:
                for key in ('body', 'text'):
                    if key in out:
                        out[key] = ''
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
