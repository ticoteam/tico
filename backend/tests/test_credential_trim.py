"""The one-time credential-use trim (store.sweep_credential_uses): old audit rows to the grain record_use writes now."""
import json
import math

import pytest

from backend import store as store_module
from backend.config import Settings
from backend.store import TRIM_BATCH, TRIM_PAUSE, H, Store, sweep_credential_uses

BASE = "2026-10-01T10:00:00.000000Z"
REVEAL, SENT = "credential.revealed", "credential.sent_to_computer"


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(store_module, "_sleep", lambda seconds: None)


def store(path):
    s = Store(Settings(db_path=path))
    s.initialize(seed_market=False)
    return s


def at(minutes):
    return H.shift(BASE, minutes=minutes)


def row(name, minutes, actor, action, target, detail):
    return (name, at(minutes), actor, action, target, json.dumps(detail) if detail is not None else None)


def add(s, rows):
    with s.transaction() as c:
        c.executemany("INSERT INTO events(id,created_at,actor,action,target,detail) VALUES(?,?,?,?,?,?)", rows)


def remaining(s):
    """The rows this file wrote (the store's own start-up rows have other targets)."""
    with s.read() as c:
        return {r[0] for r in c.execute("SELECT id FROM events WHERE target LIKE 'cred-%'")}


def watcher(name, minutes, bot, actor="runner:r1", target="cred-1"):
    return row(name, minutes, actor, SENT, target, {"bot": bot, "runner": "r1", "use": "watcher"})


def test_a_pair_every_five_minutes_for_two_and_a_half_hours_keeps_three(tmp_path):
    s = store(tmp_path / "hub.sqlite")
    add(s, [watcher(f"w{m}", m, "ops") for m in range(0, 151, 5)])
    assert sweep_credential_uses(s, now=at(1440)) == 28
    assert remaining(s) == {"w0", "w60", "w120"}


def test_a_person_reveal_is_never_deleted(tmp_path):
    s = store(tmp_path / "hub.sqlite")
    add(s, [row(f"p{m}", m, "human:chris", REVEAL, "cred-1", {"attempt": None}) for m in range(0, 151, 5)] +
           [row(f"b{m}", m, "bot:ops", REVEAL, "cred-1", {"attempt": "att-1"}) for m in range(0, 151, 5)])
    assert sweep_credential_uses(s, now=at(1440)) == 28
    kept = remaining(s)
    assert {f"p{m}" for m in range(0, 151, 5)} <= kept
    assert {i for i in kept if i.startswith("b")} == {"b0", "b60", "b120"}


def test_two_bots_on_one_credential_each_keep_their_own_rows(tmp_path):
    s = store(tmp_path / "hub.sqlite")
    rows = []
    for m in range(0, 151, 5):
        rows.append(watcher(f"alpha{m}", m, "alpha"))            # one actor, two bots, interleaved
        rows.append(watcher(f"beta{m}", m + 2, "beta"))
        rows.append(row(f"ra{m}", m, "bot:alpha", REVEAL, "cred-1", {"attempt": "a"}))   # two actors, each its own bot
        rows.append(row(f"rb{m}", m + 2, "bot:beta", REVEAL, "cred-1", {"attempt": "b"}))
    add(s, rows)
    assert sweep_credential_uses(s, now=at(1440)) == 4 * 31 - 12
    assert remaining(s) == {"alpha0", "alpha60", "alpha120", "beta0", "beta60", "beta120",
                            "ra0", "ra60", "ra120", "rb0", "rb60", "rb120"}


def test_rows_record_use_always_writes_stay_and_count_as_the_last_write(tmp_path):
    s = store(tmp_path / "hub.sqlite")
    rows = [row(f"w{m}", m, "bot:ops", REVEAL, "cred-1", {"attempt": "att-1"}) for m in range(0, 121, 5)]
    rows.append(row("forced30", 30, "bot:ops", REVEAL, "cred-1", {"attempt": None}))     # written, so it resets the window
    rows += [row(f"fin{m}", m, "bot:fin", REVEAL, "cred-1", {"attempt": None}) for m in range(0, 151, 5)]
    add(s, rows)
    # Windowed rows after the forced write at 30 min: 60 is 30 min on (dropped), 90 is 60 min on (kept), 120 is 30 min on.
    assert sweep_credential_uses(s, now=at(1440)) == 23
    assert remaining(s) == {"w0", "forced30", "w90"} | {f"fin{m}" for m in range(0, 151, 5)}


def test_rows_at_or_after_the_first_pass_are_not_walked(tmp_path):
    s = store(tmp_path / "hub.sqlite")
    add(s, [watcher(f"w{m}", m, "ops") for m in range(0, 31, 5)])
    assert sweep_credential_uses(s, now=at(10)) == 1          # w5 goes; w10 and later are the deploy's, left alone
    assert remaining(s) == {"w0", "w10", "w15", "w20", "w25", "w30"}


def build_many(s):
    rows = []
    for c in range(4):
        target = f"cred-{c}"
        rows.append(row(f"c{c}-p", 7, "human:chris", REVEAL, target, {"attempt": None}))
        rows.append(watcher(f"c{c}-tie", 0, "ops", target=target))     # the same pair and ts as the first send
        for m in range(0, 301, 5):
            rows.append(watcher(f"c{c}-w{m}", m, "ops", target=target))
            rows.append(row(f"c{c}-r{m}", m + 1, "bot:ops", REVEAL, target, {"attempt": f"a{m}"}))
    add(s, rows)


def test_a_capped_and_resumed_trim_matches_one_full_pass(tmp_path):
    full, capped = store(tmp_path / "full.sqlite"), store(tmp_path / "capped.sqlite")
    build_many(full)
    build_many(capped)
    deleted_full = sweep_credential_uses(full, now=at(1440))
    assert deleted_full > 0
    total, calls = 0, 0
    while True:
        calls += 1
        n = sweep_credential_uses(capped, now=at(1440), batch=3, most=7, page=4)
        assert n <= 7
        total += n
        if n == 0:
            break
    assert calls > 5, "the cap must have split the work across passes"
    assert total == deleted_full
    assert remaining(capped) == remaining(full)
    with capped.read() as c:
        assert '"done": true' in c.execute("SELECT value FROM registry_metadata WHERE key='credential-use-trim'"
                                           ).fetchone()[0]


def test_a_second_full_pass_deletes_nothing(tmp_path):
    s = store(tmp_path / "hub.sqlite")
    build_many(s)
    assert sweep_credential_uses(s, now=at(1440)) > 0
    before = remaining(s)
    assert sweep_credential_uses(s, now=at(1440)) == 0         # done: one read
    with s.transaction() as c:
        c.execute("DELETE FROM registry_metadata WHERE key='credential-use-trim'")
    assert sweep_credential_uses(s, now=at(1440)) == 0         # a full walk of what is left finds nothing to delete
    assert remaining(s) == before


def test_each_flush_is_followed_by_one_pause_and_no_batch_is_over_trim_batch(tmp_path, monkeypatch):
    s = store(tmp_path / "hub.sqlite")
    add(s, [watcher(f"w{m}", m, "ops") for m in range(500)])        # one a minute: most are inside the hour and go
    sleeps, sizes, flush = [], [], store_module._trim_flush

    def record_flush(store, rowids):
        sizes.append(len(rowids))
        return flush(store, rowids)

    monkeypatch.setattr(store_module, "_sleep", sleeps.append)
    monkeypatch.setattr(store_module, "_trim_flush", record_flush)
    deleted = sweep_credential_uses(s, now=at(1440))
    assert deleted > 2 * TRIM_BATCH
    assert sum(sizes) == deleted
    assert max(sizes) <= TRIM_BATCH
    assert len(sizes) == math.ceil(deleted / TRIM_BATCH)
    assert [x for x in sleeps if x == TRIM_PAUSE] == [TRIM_PAUSE] * len(sizes)
