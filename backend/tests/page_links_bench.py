"""Repeatable SQL plans and median timings: python3 -m backend.tests.page_links_bench (RunsOn)."""

import json
import statistics
import sqlite3
import tempfile
import time
from pathlib import Path

from backend import page_links
from backend.store import H
from backend.tests.helpers.company import company_store

N = 20_000
REPEATS = 15


def main():
    with tempfile.TemporaryDirectory() as folder:
        store = company_store(Path(folder))
        with store.read() as c:
            # Measure the SQL on an isolated fixture, without task-engine maintenance over synthetic task ids.
            c.execute("BEGIN")
            now = H.now()
            rows = [
                (
                    f'file-{i:024x}',
                    'ops',
                    'task:t' + str(i % 100),
                    str(i),
                    'Page ' + str(i),
                    'file',
                    'text/html',
                    'tico_blob',
                    't' + str(i % 100),
                    1,
                    now,
                    now,
                )
                for i in range(N)
            ]
            c.executemany(
                'INSERT INTO bot_files(id,bot_id,scope,identity,title,kind,mime,locator,task_id,current_version,first_activity_at,last_activity_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                rows,
            )
            c.executemany(
                'INSERT INTO pages(file_id,created_at,updated_at) VALUES(?,?,?)', [(r[0], now, now) for r in rows]
            )
            c.execute(
                "INSERT INTO blobs(id,owner,digest,size,name,content_type,created_at) VALUES('bench','bot:ops',?,1,'spec.html','text/html',?)",
                ('0' * 64, now),
            )
            c.executemany(
                "INSERT INTO bot_file_versions(file_id,version,blob_id,digest,size,name,mime,actor,created_at) VALUES(?,1,'bench',?,1,'spec.html','text/html','bot:ops',?)",
                [(r[0], '0' * 64, now) for r in rows],
            )
            links = []
            for i, r in enumerate(rows):
                links.extend(
                    [
                        (f'w{i}', r[0], 'bot:ops', 'writer', 'bot:ops', now),
                        (f't{i}', r[0], 'task:t' + str(i % 100), 'Spec', 'bot:ops', now),
                    ]
                )
                if i < N // 2:
                    links.append((f'p{i}', r[0], 'human:ben', 'writer', 'bot:ops', now))
            c.executemany('INSERT INTO page_links VALUES(?,?,?,?,?,?)', links)
            c.executemany(
                'INSERT INTO file_text_state(doc,file_id,version,indexed_at) VALUES(?,?,1,?)',
                [(i + 1, r[0], now) for i, r in enumerate(rows)],
            )
            c.executemany(
                'INSERT INTO file_text(rowid,title,text) VALUES(?,?,?)',
                [(i + 1, r[4], 'Orbitalcraft report') for i, r in enumerate(rows)],
            )
            c.execute('ANALYZE')
            c.commit()
        with store.read_transaction() as c:
            print(
                json.dumps(
                    {
                        'bot_files': c.execute('SELECT count(*) FROM bot_files').fetchone()[0],
                        'page_links': c.execute('SELECT count(*) FROM page_links').fetchone()[0],
                    }
                )
            )
            select = 'SELECT f.* FROM pages p JOIN bot_files f ON f.id=p.file_id WHERE f.state<>\'deleted\' AND p.archived=0 AND f.archived=0'
            order = ' ORDER BY f.last_activity_at DESC,f.id DESC LIMIT 50'
            library = 'SELECT f.*,p.cadence AS page_cadence,p.archived AS page_archived FROM bot_files f LEFT JOIN pages p ON p.file_id=f.id WHERE f.state<>\'deleted\' AND f.archived=0 AND COALESCE(p.archived,0)=0'
            old_task = "(f.task_id=? OR EXISTS (SELECT 1 FROM pins WHERE place=? AND item='page:' || f.id))"
            new_task = (
                "f.id IN (SELECT id FROM bot_files WHERE task_id=? UNION "
                "SELECT page_id FROM page_links WHERE target=? UNION "
                "SELECT substr(item,6) FROM pins WHERE place=? AND item LIKE 'page:%')"
            )
            cases = [
                ('pages/task/old', select + ' AND ' + old_task + order, ('t42', 'task:t42')),
                ('pages/task/new', select + ' AND ' + new_task + order, ('t42', 'task:t42', 'task:t42')),
                ('pages/unfiltered', select + order, ()),
                ('library/unfiltered', library + order, ()),
            ]
            cases.extend(
                [
                    ('pages/bot/old', select + ' AND f.bot_id=?' + order, ('ops',)),
                    (
                        'pages/bot/new',
                        select
                        + ' AND f.id IN (SELECT id FROM bot_files WHERE bot_id=? UNION SELECT page_id FROM page_links WHERE target=?)'
                        + order,
                        ('ops', 'bot:ops'),
                    ),
                    (
                        'publish/as',
                        "SELECT f.* FROM page_links l JOIN pages p ON p.file_id=l.page_id JOIN bot_files f ON f.id=p.file_id WHERE l.target=? AND l.role=? AND f.state<>'deleted'",
                        ('task:t42', 'Spec'),
                    ),
                ]
            )
            for label, filters in [
                ('linked', ('task:t42', '', '')),
                ('role', ('', 'Spec', '')),
                ('writer', ('', '', 'human:ben')),
                ('combined', ('task:t42', 'Spec', 'bot:ops')),
            ]:
                where, args = page_links.filters(*filters)
                for kind, base in [('pages', select), ('library', library)]:
                    cases.append((kind + '/' + label, base + ' AND ' + ' AND '.join(where) + order, args))
            fts = (
                "SELECT f.*,p.cadence AS page_cadence,file_text.text AS text FROM bot_files f "
                "LEFT JOIN pages p ON p.file_id=f.id JOIN file_text_state s ON s.file_id=f.id "
                "JOIN file_text ON file_text.rowid=s.doc WHERE f.state<>'deleted' AND f.archived=0 "
                "AND COALESCE(p.archived,0)=0 AND ({where}) AND file_text MATCH ? "
                "ORDER BY bm25(file_text,10.0,1.0) LIMIT 500"
            )
            cases.append(('search/old', fts.format(where='1'), ('orbitalcraft',)))
            forced_fts = fts.replace(
                "FROM bot_files f LEFT JOIN pages p ON p.file_id=f.id JOIN file_text_state s ON s.file_id=f.id JOIN file_text ON file_text.rowid=s.doc",
                "FROM file_text CROSS JOIN file_text_state s ON s.doc=file_text.rowid CROSS JOIN bot_files f ON f.id=s.file_id LEFT JOIN pages p ON p.file_id=f.id",
            )
            for label, filters in [
                ('linked', ('task:t42', '', '')),
                ('role', ('', 'Spec', '')),
                ('writer', ('', '', 'human:ben')),
            ]:
                where, args = page_links.filters(*filters)
                cases.append(
                    ('search/' + label + '/unforced', fts.format(where=' AND '.join(where)), (*args, 'orbitalcraft'))
                )
                cases.append(('search/' + label, forced_fts.format(where=' AND '.join(where)), (*args, 'orbitalcraft')))
            context = (
                "SELECT f.*,p.audience,coalesce(v.created_at,f.last_activity_at) AS context_updated_at "
                "FROM pages p JOIN bot_files f ON f.id=p.file_id LEFT JOIN bot_file_versions v "
                "ON v.file_id=f.id AND v.version=f.current_version WHERE p.archived=0 AND f.archived=0 "
                "AND f.state<>'deleted' AND ({where}) ORDER BY context_updated_at DESC,f.id DESC"
            )
            old_context = (
                "f.task_id=? OR f.scope=? OR EXISTS (SELECT 1 FROM pins WHERE place=? AND item='page:' || f.id)"
            )
            cases.extend(
                [
                    ('context/old', context.format(where=old_context), ('t42', 'task:t42', 'task:t42')),
                    (
                        'context/new',
                        context.format(
                            where=old_context + ' OR f.id IN (SELECT page_id FROM page_links WHERE target=?)'
                        ),
                        ('t42', 'task:t42', 'task:t42', 'task:t42'),
                    ),
                    (
                        'unlink/last-writer',
                        "SELECT count(*) FROM page_links WHERE page_id=? AND role='writer'",
                        (rows[42][0],),
                    ),
                    (
                        'publish/linked-documents',
                        "SELECT target,role FROM page_links WHERE page_id=? AND target LIKE 'task:%' AND role NOT IN ('writer','related')",
                        (rows[42][0],),
                    ),
                ]
            )
            old_attach = "SELECT f.* FROM bot_files f JOIN bot_file_versions v ON v.file_id=f.id AND v.version=f.current_version WHERE f.task_id=? AND f.archived=0 AND v.name=? AND NOT EXISTS (SELECT 1 FROM task_documents d WHERE d.file_id=f.id) ORDER BY f.last_activity_at DESC"
            cases.extend(
                [
                    ('attach/old', old_attach, ('t42', 'spec.html')),
                    (
                        'attach/new',
                        old_attach.replace(
                            'SELECT 1 FROM task_documents d WHERE d.file_id=f.id',
                            "SELECT 1 FROM page_links pl WHERE pl.page_id=f.id AND pl.role NOT IN ('writer','related')",
                        ),
                        ('t42', 'spec.html'),
                    ),
                    (
                        'task-files/old',
                        "SELECT f.* FROM bot_files f WHERE f.locator='tico_blob' AND f.task_id=? ORDER BY f.last_activity_at DESC",
                        ('t42',),
                    ),
                    (
                        'task-files/all',
                        "SELECT f.* FROM bot_files f WHERE f.locator='tico_blob' AND (f.task_id=? OR f.id IN (SELECT page_id FROM page_links WHERE target=?)) ORDER BY f.last_activity_at DESC",
                        ('t42', 'task:t42'),
                    ),
                    ('links', page_links.LINKS_SQL, (rows[42][0],)),
                    ('writers', page_links.WRITERS_SQL, (rows[42][0],)),
                    (
                        'approval/history',
                        'SELECT file_id,version FROM requirement_pages WHERE visit_id=? AND requirement_id=? AND version=? AND created_at<=? ORDER BY created_at DESC,rowid DESC LIMIT 1',
                        ('v', 'r', 1, now),
                    ),
                ]
            )
            for label, sql, args in cases:
                plan = [row[3] for row in c.execute('EXPLAIN QUERY PLAN ' + sql, args)]
                took = []
                timed_out = False
                try:
                    for _ in range(REPEATS + 1):
                        deadline = time.monotonic() + 2
                        c.set_progress_handler(lambda: int(time.monotonic() > deadline), 10_000)
                        start = time.perf_counter()
                        c.execute(sql, args).fetchall()
                        took.append((time.perf_counter() - start) * 1000)
                except sqlite3.OperationalError as exc:
                    if 'interrupted' not in str(exc):
                        raise
                    timed_out = True
                finally:
                    c.set_progress_handler(None, 0)
                print(
                    json.dumps(
                        {
                            'query': label,
                            'median_ms': round(statistics.median(took[1:]), 3) if len(took) > 1 else None,
                            'exceeded_2000_ms': timed_out,
                            'plan': plan,
                        }
                    ),
                    flush=True,
                )


if __name__ == '__main__':
    main()
