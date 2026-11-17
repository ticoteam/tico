"""Repeatable SQL plans and median timings: python3 -m backend.tests.page_links_bench (RunsOn)."""

import json
import statistics
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
        with store.transaction() as c:
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
            c.execute('ANALYZE')
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
            new_task = "(f.task_id=? OR f.id IN (SELECT page_id FROM page_links WHERE target=?) OR EXISTS (SELECT 1 FROM pins WHERE place=? AND item='page:' || f.id))"
            cases = [
                ('pages/task/old', select + ' AND ' + old_task + order, ('t42', 'task:t42')),
                ('pages/task/new', select + ' AND ' + new_task + order, ('t42', 'task:t42', 'task:t42')),
                ('pages/unfiltered', select + order, ()),
                ('library/unfiltered', library + order, ()),
            ]
            for label, filters in [
                ('linked', ('task:t42', '', '')),
                ('role', ('', 'Spec', '')),
                ('writer', ('', '', 'human:ben')),
                ('combined', ('task:t42', 'Spec', 'bot:ops')),
            ]:
                where, args = page_links.filters(*filters)
                for kind, base in [('pages', select), ('library', library)]:
                    cases.append((kind + '/' + label, base + ' AND ' + ' AND '.join(where) + order, args))
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
                c.execute(sql, args).fetchall()
                took = []
                for _ in range(REPEATS):
                    start = time.perf_counter()
                    c.execute(sql, args).fetchall()
                    took.append((time.perf_counter() - start) * 1000)
                print(json.dumps({'query': label, 'median_ms': round(statistics.median(took), 3), 'plan': plan}))


if __name__ == '__main__':
    main()
