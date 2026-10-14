"""The runner's event stream (runner/runner_events.py): an event runs only the read it names; with no
events, only the backup pass; while the stream is down, the old polling until it is back; and a
server without the stream is polled as before."""
import threading
from unittest import mock

from runner import runner_events as RE
from runner import service
from runner.service import Runner


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class Client:
    def __init__(self):
        self.gets = []

    def get(self, path, **query):
        self.gets.append(path)
        return {}


def runner(events):
    r = Runner.__new__(Runner)
    r.client, r.events = Client(), events
    return r


def up(client=None):
    events = RE.Events(client)
    events.state = "up"
    return events


def test_an_event_runs_only_the_read_it_names_and_otherwise_only_the_backup_pass():
    clock = Clock()
    events = up()
    r = runner(events)
    with mock.patch.object(service.time, "monotonic", clock):
        r.poll_credential_imports()                  # the first pass reads everything once
        clock.now += 60
        r.poll_credential_imports()                  # the stream is up and said nothing: no read
        assert r.client.gets == ["runner-credential-imports"]
        events.ask(*RE.CHANNELS["credential_imports"], *RE.CHANNELS["work"])
        r.poll_credential_imports()
        r.poll_credential_imports()
        assert r.client.gets == ["runner-credential-imports"] * 2
        assert events.take("claim") and not events.take("sync")    # work is a claim's, and nothing else's
        clock.now += RE.BACKUP_POLL_S
        r.poll_credential_imports()
        assert r.client.gets == ["runner-credential-imports"] * 3


def test_claims_wait_for_work_and_follow_up_an_empty_one_backing_off():
    clock = Clock()
    events = up()
    r = runner(events)
    with mock.patch.object(service.time, "monotonic", clock):
        r.next_claim = clock.now + 2
        r._claim_live_at = clock.now + RE.BACKUP_POLL_S
        assert not r.claim_due()
        events.ask("claim")
        assert r.claim_due()
        r.next_claim = clock.now + 2
        r.claimed(False)                       # the bot is mid-turn: ask again soon, then less often
        clock.now += service.CLAIM_IDLE_MAX
        assert r.claim_due()
        r.claimed(False)
        clock.now += service.CLAIM_IDLE_MAX
        assert not r.claim_due()
        clock.now += service.CLAIM_HINT_S
        r.claimed(False)                       # past the follow-up window: only the backup pass
        clock.now += RE.BACKUP_POLL_S - 1
        assert not r.claim_due()
        r.next_claim = 0                       # capacity came free
        assert r.claim_due()


def test_stream_down_polls_as_before_and_reconnects_from_the_last_event():
    clock = Clock()
    seen, gate = [], threading.Event()

    def connect(client, after):
        seen.append(after)
        if len(seen) == 1:
            yield "ready", None, {"seq": 7, "release": "0.3.28"}
            yield "runner", "9", {"seq": 9, "kind": "logins"}
            raise OSError("connection reset")
        gate.wait(5)
        yield "ready", None, {"seq": 12, "release": "0.3.28"}
        yield "keepalive", None, {}
        events.stop.set()

    events = RE.Events(object(), connect=connect)
    r = runner(events)
    with mock.patch.object(service.time, "monotonic", clock), mock.patch.object(events.stop, "wait", lambda t: None):
        thread = threading.Thread(target=events.run)
        thread.start()
        deadline = 200
        while events.state != "down" and deadline:
            threading.Event().wait(0.01)
            deadline -= 1
        assert events.state == "down" and events.take("logins")
        r.poll_credential_imports()
        clock.now += service.CREDENTIAL_IMPORT_POLL_S
        r.poll_credential_imports()                   # down: on the old timer
        assert r.client.gets == ["runner-credential-imports"] * 2
        gate.set()
        thread.join(5)
    assert seen == [None, "9"]                        # resumed after the last event it was sent
    assert events.state == "up"
    assert all(events.take(channel) for channel in RE.ALL)   # back after an outage: one full pass


def test_a_server_without_the_stream_is_polled_as_before():
    def connect(client, after):
        raise RE.NotSupported()
        yield

    events = RE.Events(object(), connect=connect)
    events.stop.wait = lambda t: events.stop.set()
    events.run()
    assert events.state == "legacy" and not events.live
    clock = Clock()
    r = runner(events)
    with mock.patch.object(service.time, "monotonic", clock):
        r.next_claim = clock.now
        assert r.claim_due()                          # the claim timer, as before
        r.poll_credential_imports()
        clock.now += service.CREDENTIAL_IMPORT_POLL_S
        r.poll_credential_imports()
    assert r.client.gets == ["runner-credential-imports"] * 2


def test_an_unset_capacity_runs_five_turns_per_core_as_memory_allows_and_a_set_one_is_kept():
    from unittest import mock
    from runner import service

    def sized(cpus, gb):
        pages = {"SC_PAGE_SIZE": 4096, "SC_PHYS_PAGES": int(gb * 2**30 / 4096)}
        with mock.patch.object(service.os, "cpu_count", return_value=cpus), \
                mock.patch.object(service.os, "sysconf", side_effect=pages.__getitem__):
            return service.machine_capacity()
    assert sized(2, 16) == 10                 # the turns wait on the model, not the two cores
    assert sized(2, 2) == 6                   # memory caps a small box
    assert sized(1, 1) == 4                   # never below what every runner had
    assert sized(64, 512) == 64
    assert service.machine_capacity(80) == 80 and service.machine_capacity("auto") >= 4
    assert service.machine_capacity(1000) == 256


def test_a_server_from_before_larger_capacities_still_gets_a_heartbeat_at_32():
    from unittest.mock import Mock
    from clients.tico import APIError
    runner = Runner.__new__(Runner)
    runner.client = Mock()
    runner.client.post.side_effect = [APIError("validation", "body.capacity: Input should be less than or equal to 32", 422),
                                      {"ok": True}]
    body = {"capacity": 60, "readiness": {}}
    assert runner.report_heartbeat(body) == {"ok": True}
    assert runner.client.post.call_args.args[1]["capacity"] == 32
