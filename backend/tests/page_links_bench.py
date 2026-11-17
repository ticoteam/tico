"""Repeatable SQL plans and median timings: python3 -m backend.tests.page_links_bench (RunsOn)."""

import json
import statistics
import sqlite3
import tempfile
import time
from importlib import import_module
from pathlib import Path

from backend import files, page_links, task_context, task_documents, task_pages
from backend.store import H
from backend.tests.helpers.company import company_store

N = 20_000
REPEATS = 15


def seed_approvals(c, now, rows):
    c.execute("INSERT INTO pipelines(id,name,created_at,updated_at) VALUES('bench','Bench',?,?)", (now, now))
    c.execute(
        "INSERT INTO stages(id,pipeline_id,position,kind,name,created_at,updated_at) VALUES('bench','bench',0,'work','Bench',?,?)",
        (now, now),
    )
    c.execute(
        "INSERT INTO requirements(id,stage_id,kind,settings,created_at) VALUES('bench','bench','page',?,?)",
        (json.dumps({'document': 'Spec'}), now),
    )
    visits = [(f'v{i}', f't{i % 100}', now, now) for i in range(800)]
    c.executemany(
        "INSERT INTO visits(id,task_id,stage_id,entered_at,entered_how,left_at,how) "
        "VALUES(?,?,'bench',?,'new',?,'met')",
        visits,
    )
    answers = [(f'a{i}', visit, now) for i, (visit, _, _, _) in enumerate(visits)]
    c.executemany(
        "INSERT INTO answers(id,visit_id,requirement_id,by_id,answer,page_version,created_at) "
        "VALUES(?,?,'bench','human:ben','ok',1,?)",
        answers,
    )
    c.executemany(
        "INSERT INTO requirement_pages(id,visit_id,requirement_id,file_id,version,attached_by_id,created_at) "
        "VALUES(?,?,'bench',?,1,'bot:ops',?)",
        [(f'sub-{i}', visit, rows[i][0], now) for i, (visit, _, _, _) in enumerate(visits)],
    )
    c.executemany(
        "INSERT INTO history_events(id,subject_type,subject_id,kind,data,created_at) VALUES(?,'task',?,'answer',?,?)",
        [
            (
                f'event-{i}',
                task,
                json.dumps(
                    {
                        'answer_id': answers[i][0],
                        **({'pages': [{'file_id': rows[i][0], 'version': 1}]} if i % 2 else {}),
                    }
                ),
                now,
            )
            for i, (_, task, _, _) in enumerate(visits)
        ],
    )


def compare_approvals(c):
    task_ids = [f't{i}' for i in range(50)]
    sql = task_documents.APPROVALS_SQL.format(marks=','.join('?' * len(task_ids)))
    plan = [row[3] for row in c.execute('EXPLAIN QUERY PLAN ' + sql, (*task_ids, *task_ids))]
    old_sql = (
        "SELECT a.*,json_extract(r.settings,'$.document') AS document FROM visits v "
        "JOIN answers a ON a.visit_id=v.id JOIN requirements r ON r.id=a.requirement_id "
        "WHERE v.task_id=? AND a.answer='ok' AND a.undone_at IS NULL ORDER BY a.created_at DESC,a.rowid DESC"
    )
    old_plan = [row[3] for row in c.execute('EXPLAIN QUERY PLAN ' + old_sql, (task_ids[0],))]

    def previous():
        out = {}
        for task_id in task_ids:
            approved = out[task_id] = {}
            for answer in c.execute(old_sql, (task_id,)):
                if answer['document']:
                    for fid, version in task_pages.answer_pages(c, task_id, dict(answer)).items():
                        approved.setdefault((fid, answer['document']), version)
        return out

    assert previous() == task_documents._read_approvals(c, task_ids)
    for kind, read, query_plan in [
        ('per-task/old', previous, old_plan),
        ('batched/new', lambda: task_documents._read_approvals(c, task_ids), plan),
    ]:
        elapsed = []
        for _ in range(REPEATS):
            start = time.perf_counter()
            read()
            elapsed.append((time.perf_counter() - start) * 1000)
        print(
            json.dumps(
                {'query': 'approvals/' + kind, 'median_ms': round(statistics.median(elapsed), 3), 'plan': query_plan}
            )
        )


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
            c.executemany(
                "INSERT INTO bot_file_versions(file_id,version,blob_id,digest,size,name,mime,actor,created_at) VALUES(?,?,'bench',?,1,'spec.html','text/html','bot:ops',?)",
                [(r[0], v, '0' * 64, now) for r in rows[:200] for v in range(2, 52)],
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
            seed_approvals(c, now, rows)
            c.executemany(
                "INSERT INTO pins(id,place,item,position,made_by,created_at) VALUES(?,'task:t42',?,?,'human:ana',?)",
                [(f'pin-{i}', 'page:' + rows[i][0], i, now) for i in range(30)],
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
                    ('task-files/all', *files.task_listing_query('t42', True)),
                    ('task-files/versions', files.TASK_VERSIONS_SQL, (json.dumps([r[0] for r in rows[:200]]), 50, 49)),
                    ('task-files/markdown', task_documents.TASK_MARKDOWN_SQL, ('t42',)),
                    ('document/candidates', task_documents.TASK_DOCUMENTS_SQL, ('task:t42',)),
                    ('context/candidates', *task_context.pages_query('t42')),
                    ('links', page_links.LINKS_SQL, (rows[42][0],)),
                    ('writers', page_links.WRITERS_SQL, (rows[42][0],)),
                    (
                        'approval/history',
                        'SELECT file_id,version FROM requirement_pages WHERE visit_id=? AND requirement_id=? AND version=? AND created_at<=? ORDER BY created_at DESC,rowid DESC LIMIT 1',
                        ('v', 'r', 1, now),
                    ),
                ]
            )
            # Model the role-only read filter with 90 hidden targets in the fixture.
            c.create_function('page_link_target_readable', 1, lambda target: target.endswith('2'))
            clauses, args = page_links.filters(role='Spec', check_role_target=True)
            cases.append(
                (
                    'list/visible-roles',
                    "SELECT f.id FROM bot_files f WHERE " + " AND ".join(clauses) + " LIMIT 50",
                    args,
                )
            )
            migration = import_module('backend.migrations.20261117195614_page_links')
            cases.append(('backfill/document-roles', migration.DOCUMENT_ROLES, ()))
            cases.append(('backfill/pins', migration.PINS, ()))
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
            compare_approvals(c)


if __name__ == '__main__':
    main()
