"""Standalone pages keep their identity and their original read boundary across writers and links."""

import sqlite3
from pathlib import Path

import pytest

from backend import page_links as L
from backend.store import Problem

MIGRATION = Path(__file__).parents[1] / 'migrations/20261117195614_page_links.sql'


def test_backfill_is_idempotent_and_adopts_documents_and_people():
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    c.executescript('''
        CREATE TABLE requirement_pages(visit_id TEXT, requirement_id TEXT, file_id TEXT, replaced_at TEXT, created_at TEXT);
        CREATE TABLE bot_files(id TEXT PRIMARY KEY,bot_id TEXT,task_id TEXT);
        CREATE TABLE pages(file_id TEXT PRIMARY KEY,cadence TEXT,created_at TEXT,updated_at TEXT,spec TEXT);
        CREATE TABLE pins(place TEXT,item TEXT,made_by TEXT,created_at TEXT);
        CREATE TABLE task_documents(file_id TEXT,task_id TEXT,name TEXT,created_at TEXT,updated_at TEXT);
        CREATE TABLE bot_file_versions(file_id TEXT,actor TEXT,created_at TEXT);
        INSERT INTO bot_files VALUES('p','ops','private'),('q','finance',NULL),('doc','ops','t');
        INSERT INTO pages VALUES('p','weekly','2026-01-01','2026-01-02','yes'),
                                ('q','none','2026-01-01','2026-01-01',NULL);
        INSERT INTO pins VALUES('task:public','page:p','human:ana','2026-01-02');
        INSERT INTO task_documents VALUES('doc','t','QA report','2026-01-01','2026-01-02');
        INSERT INTO bot_file_versions VALUES('p','human:ben','2026-01-02'),('doc','human:ana','2026-01-02');
    ''')
    for _ in range(2):
        c.executescript(MIGRATION.read_text())
    assert {tuple(r) for r in c.execute('SELECT page_id,target,role FROM page_links')} == {
        ('p', 'bot:ops', 'writer'),
        ('q', 'bot:finance', 'writer'),
        ('p', 'task:private', 'Spec'),
        ('p', 'task:public', 'related'),
        ('doc', 'task:t', 'QA report'),
        ('doc', 'task:t', 'related'),
        ('doc', 'bot:ops', 'writer'),
        ('p', 'human:ben', 'writer'),
        ('doc', 'human:ana', 'writer'),
    }
    assert c.execute("SELECT cadence FROM pages WHERE file_id='doc'").fetchone()[0] == 'none'
    c.execute(
        "INSERT INTO requirement_pages(visit_id,requirement_id,file_id,replaced_at) VALUES('v','r','p',NULL),('v','r','q',NULL)"
    )
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("INSERT INTO requirement_pages(visit_id,requirement_id,file_id,replaced_at) VALUES('v','r','p',NULL)")
    assert L.link(c, 'p', 'task:extra', 'qa-report', 'human:ana')
    assert not L.link(c, 'p', 'task:extra', 'QA report', 'human:ana')
    assert L.pages_for(c, 'task:extra', 'qa-report') == ['p']
    L.unlink(c, 'q', 'human:nobody', 'writer', 'human:ana')
    with pytest.raises(Problem, match='at least one writer'):
        L.unlink(c, 'q', 'bot:finance', 'writer', 'human:ana')
    for target, role in [('goal:g', 'Spec'), ('task:t', 'writer'), ('wrong:id', 'related')]:
        with pytest.raises(Problem):
            L.link(c, 'p', target, role, 'human:ana')
