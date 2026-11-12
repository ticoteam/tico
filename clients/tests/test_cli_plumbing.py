"""The `tico` CLI's plumbing (clients/cli_plumbing.py, clients/profiles.py, clients/events_tail.py): profiles, personal
tokens, --fields, exit codes and `tico events tail`. No network: the API is a stub."""

import io
import json
import os
import stat

import pytest

from clients import cli_plumbing, events_tail, hubcli, hubtools, profiles, remotecli
from clients.tico import APIError

TOKEN = "tico_pt_" + "s" * 40


@pytest.fixture(autouse=True)
def shell(tmp_path, monkeypatch):
    """A person's shell: no runner variables, profiles in a temporary file."""
    for name in ("HUB_API_URL", "HUB_TOKEN", "HUB_BOT", "HUB_EMPLOYEE", "TICO_PROFILE", "HUB_OPERATION_ID"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TICO_CONFIG", str(tmp_path / "tico" / "profiles.json"))
    monkeypatch.setenv("HUB_RUNNER_CONFIG", str(tmp_path / "no-runner.json"))
    profiles.ACTIVE.update(profile=None, source=None)
    yield tmp_path
    profiles.ACTIVE.update(profile=None, source=None)


class Api:
    """A stub of clients/tico.py Client: records calls, answers from `answers` by path."""

    calls = []
    answers = {}

    def __init__(self, url, token, timeout=15, retries=3):
        self.url, self.token = url.rstrip("/"), token
        self.__dict__["_tico_is_botops"] = False

    def get(self, path, /, **query):
        Api.calls.append(("GET", path, self.url, self.token))
        answer = Api.answers.get(path, {})
        if isinstance(answer, Exception):
            raise answer
        return answer

    def post(self, path, body=None, key=None):
        Api.calls.append(("POST", path, body))
        return Api.answers.get(path, {})

    def call(self, method, path, body=None, key=None, query=None, delegate=False):
        Api.calls.append((method, path, body))
        return Api.answers.get(path, {})


@pytest.fixture
def api(monkeypatch):
    Api.calls, Api.answers = (
        [],
        {
            "me": {"actor": "human:ana", "role": "owner", "kind": "owner"},
            "config": {"company_name": "Acme", "release": "v0.6.0"},
        },
    )
    monkeypatch.setattr(remotecli, "Client", Api)
    monkeypatch.setattr("clients.tico.Client", Api)
    return Api


def token_file(tmp_path, name="acme_token", token=TOKEN):
    path = tmp_path / name
    path.write_text(token + "\n")
    path.chmod(0o600)
    return str(path)


def run(capsys, *argv):
    code = hubcli.main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


# ----------------------------------------------------------------------------- profiles
def test_a_profile_from_a_token_file_is_checked_saved_600_and_never_printed(shell, api, capsys):
    path = token_file(shell)
    code, out, err = run(capsys, "auth", "add", "acme", "--url", "https://acme.tico.team/", "--token-file", path)
    assert code == 0 and TOKEN not in out + err
    saved = json.loads(out)
    assert saved["profile"] == "acme" and saved["default"] is True and saved["actor"] == "human:ana"
    assert ("GET", "me", "https://acme.tico.team", TOKEN) in api.calls  # checked with the new token before saving
    config = profiles.config_path()
    assert stat.S_IMODE(config.stat().st_mode) == 0o600 and stat.S_IMODE(config.parent.stat().st_mode) == 0o700
    assert TOKEN not in config.read_text()  # only the path is kept
    assert json.loads(config.read_text())["profiles"]["acme"] == {"url": "https://acme.tico.team", "token_file": path}
    code, out, _ = run(capsys, "auth", "list")
    assert code == 0 and TOKEN not in out
    assert json.loads(out)["profiles"] == [
        {"profile": "acme", "url": "https://acme.tico.team", "token": "file " + path, "default": True}
    ]


def test_a_failed_check_saves_nothing(shell, api, capsys):
    api.answers["me"] = APIError("unauthorized", "Invalid credential", status=401)
    code, out, _ = run(
        capsys, "auth", "add", "acme", "--url", "https://acme.tico.team", "--token-file", token_file(shell)
    )
    assert code == cli_plumbing.EXIT_SIGN_IN and json.loads(out)["error"] == "unauthorized"
    assert not profiles.config_path().exists()


def test_with_token_reads_stdin_and_a_readable_file_holding_tokens_is_refused(shell, api, capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO(TOKEN + "\n"))
    code, out, _ = run(capsys, "auth", "add", "beta", "--url", "https://beta.tico.team", "--with-token", "--no-check")
    assert code == 0 and TOKEN not in out and api.calls == []
    assert profiles.credential("beta") == ("https://beta.tico.team", TOKEN)
    profiles.config_path().chmod(0o644)
    code, out, _ = run(capsys, "--profile", "beta", "whoami")
    assert code == cli_plumbing.EXIT_SIGN_IN and "chmod 600" in json.loads(out)["detail"]


def test_a_plain_http_address_is_refused_before_any_call(shell, capsys):
    code, out, _ = run(capsys, "auth", "add", "x", "--url", "http://acme.example", "--token-file", token_file(shell))
    assert code == cli_plumbing.EXIT_USAGE and "HTTPS" in json.loads(out)["detail"]
    assert not profiles.config_path().exists()


def test_bad_profile_input_is_refused(shell, api, capsys):
    path = token_file(shell)
    code, out, _ = run(capsys, "auth", "add", "-x", "--url", "https://acme.example", "--token-file", path, "--no-check")
    assert code == cli_plumbing.EXIT_USAGE  # argparse: "-x" is a flag, not a name
    code, out, _ = run(capsys, "auth", "add", "a b", "--url", "https://acme.example", "--token-file", path)
    assert code == cli_plumbing.EXIT_SIGN_IN and "profile name" in json.loads(out)["detail"]
    code, out, _ = run(capsys, "auth", "add", "x", "--url", "https://acme.example")
    assert code == cli_plumbing.EXIT_SIGN_IN and "one way" in json.loads(out)["detail"]


def test_flag_beats_env_beats_runner_variables_beats_the_default(shell, api, capsys, monkeypatch):
    for name in ("acme", "beta"):
        profiles.add(name, f"https://{name}.tico.team", token_file=token_file(shell, name, TOKEN[:-4] + name))
    profiles.use("acme")

    def who(*argv, **env):
        """One command in a fresh shell (a profile, once applied, is in this process's environment)."""
        for name in ("HUB_API_URL", "HUB_TOKEN", "TICO_PROFILE"):
            monkeypatch.delenv(name, raising=False)
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        api.calls.clear()
        code, out, _ = run(capsys, *argv, "whoami")
        assert code == 0, out
        return json.loads(out), api.calls[0][2:]

    found, sent = who()
    assert (found["profile"], found["credential_from"]) == ("acme", "default")
    assert sent == ("https://acme.tico.team", TOKEN[:-4] + "acme")
    found, sent = who(HUB_API_URL="https://own.tico.team", HUB_TOKEN="own")
    assert (found["profile"], found["credential_from"], found["company"]) == (None, "HUB_API_URL", "Acme")
    assert sent == ("https://own.tico.team", "own")
    found, sent = who(HUB_API_URL="https://own.tico.team", HUB_TOKEN="own", TICO_PROFILE="beta")
    assert (found["profile"], found["credential_from"]) == ("beta", "TICO_PROFILE")
    assert sent == ("https://beta.tico.team", TOKEN[:-4] + "beta")
    found, sent = who("--profile", "acme", HUB_API_URL="https://own.tico.team", HUB_TOKEN="own", TICO_PROFILE="beta")
    assert (found["profile"], found["credential_from"], found["url"]) == ("acme", "flag", "https://acme.tico.team")
    # --profile may sit anywhere on the line, as gh and aws take it.
    for name in ("HUB_API_URL", "HUB_TOKEN", "TICO_PROFILE"):
        monkeypatch.delenv(name, raising=False)
    api.calls.clear()
    code, _, _ = run(capsys, "whoami", "--profile=beta")
    assert code == 0 and api.calls[0][2] == "https://beta.tico.team"


def test_a_bot_turn_keeps_its_own_token(shell, api, capsys, monkeypatch):
    profiles.add("acme", "https://acme.tico.team", token_file=token_file(shell))
    monkeypatch.setenv("HUB_BOT", "builder")
    monkeypatch.setenv("HUB_API_URL", "https://own.tico.team")
    monkeypatch.setenv("HUB_TOKEN", "turn-token")
    code, out, _ = run(capsys, "--profile", "acme", "whoami")
    assert code == cli_plumbing.EXIT_SIGN_IN and "own token" in json.loads(out)["detail"] and api.calls == []
    code, out, _ = run(capsys, "whoami")  # the default profile never replaces a turn's variables
    assert code == 0 and api.calls[0][2:] == ("https://own.tico.team", "turn-token")


def test_no_credential_is_the_sign_in_exit_and_names_profiles(shell, api, capsys):
    code, out, _ = run(capsys, "task", "list")
    assert code == cli_plumbing.EXIT_SIGN_IN and "tico auth add" in json.loads(out)["error"]
    code, out, _ = run(capsys, "--profile", "nope", "task", "list")
    assert code == cli_plumbing.EXIT_SIGN_IN and 'no profile "nope"' in json.loads(out)["detail"]


def test_use_and_remove(shell, api, capsys):
    profiles.add("acme", "https://acme.tico.team", token_file=token_file(shell))
    profiles.add("beta", "https://beta.tico.team", token_file=token_file(shell, "b"))
    assert profiles.load()["default"] == "acme"
    assert run(capsys, "auth", "use", "beta")[0] == 0 and profiles.load()["default"] == "beta"
    assert run(capsys, "auth", "remove", "beta")[0] == 0
    assert profiles.load() == {"default": None, "profiles": {"acme": profiles.load()["profiles"]["acme"]}}
    assert run(capsys, "auth", "remove", "beta")[0] == cli_plumbing.EXIT_SIGN_IN


# ----------------------------------------------------------------------------- personal tokens
def test_token_commands_run_their_tools(shell, api, capsys, monkeypatch):
    monkeypatch.setenv("HUB_API_URL", "https://acme.tico.team")
    monkeypatch.setenv("HUB_TOKEN", "t")
    api.answers["me/tokens"] = {"id": "k1", "token": TOKEN, "label": "ci", "expires_at": "2026-12-11"}
    assert run(capsys, "token", "list")[0] == 0
    assert run(capsys, "token", "list", "--all")[0] == 0
    code, out, err = run(capsys, "token", "create", "--label", "ci", "--days", "30")
    assert code == 0 and json.loads(out)["token"] == TOKEN and TOKEN not in err  # once, on stdout only
    assert run(capsys, "token", "revoke", "k1")[0] == 0
    sent = [c for c in api.calls if c[1] != "me"]
    assert sent == [
        ("GET", "me/tokens", "https://acme.tico.team", "t"),
        ("GET", "access/tokens", "https://acme.tico.team", "t"),
        ("POST", "me/tokens", {"label": "ci", "expires_in_days": 30}),
        ("POST", "me/tokens/k1/revoke", {}),
    ]
    for name in ("tico_token_list", "tico_token_create", "tico_token_revoke"):
        assert hubtools.offered_to(hubtools.BY_NAME[name]) == hubtools.PEOPLE


# ----------------------------------------------------------------------------- --fields
def test_fields_pick_from_objects_lists_and_a_page():
    task = {"id": "t1", "title": "Ship", "turn": {"state": "running"}, "labels": ["a", "b"]}
    assert profiles.pick(task, ["id", "turn.state", "labels.1", "nope"]) == {
        "id": "t1",
        "turn.state": "running",
        "labels.1": "b",
        "nope": None,
    }
    assert profiles.pick([task, task], ["id"]) == [{"id": "t1"}, {"id": "t1"}]
    page = {"tasks": [task], "next_offset": 500, "actors": {"human:a": "A"}}
    assert profiles.pick(page, ["id", "title"]) == {"tasks": [{"id": "t1", "title": "Ship"}], "next_offset": 500}
    assert profiles.pick({"tasks": [], "next_cursor": None}, ["id"]) == {"tasks": [], "next_cursor": None}
    assert profiles.pick(page, ["actors"]) == {"actors": {"human:a": "A"}}  # a key of the object wins
    assert profiles.pick("text", ["id"]) == "text"


def test_fields_is_global_and_replaces_the_text_form(shell, api, capsys, monkeypatch):
    monkeypatch.setenv("HUB_API_URL", "https://acme.tico.team")
    monkeypatch.setenv("HUB_TOKEN", "t")
    code, out, _ = run(capsys, "whoami", "--fields", "actor,company")
    assert code == 0 and json.loads(out) == {"actor": "human:ana", "company": "Acme"}
    argv, profile, fields = cli_plumbing.split_globals(
        ["--fields=a,b", "task", "list", "--profile", "x", "--", "--fields"]
    )
    assert (argv, profile, fields) == (["task", "list", "--", "--fields"], "x", ["a", "b"])


# ----------------------------------------------------------------------------- exit codes
def test_exit_codes_tell_retry_refused_sign_in_not_found_and_usage_apart(shell, api, capsys, monkeypatch):
    assert cli_plumbing.exit_code(APIError("unavailable", "x", retryable=True)) == 1
    assert cli_plumbing.exit_code(APIError("http_error", "x", status=503, retryable=True)) == 1
    assert cli_plumbing.exit_code(APIError("forbidden", "x", status=403)) == 2
    assert cli_plumbing.exit_code(APIError("validation", "x", status=422)) == 2
    assert cli_plumbing.exit_code(APIError("unauthorized", "x", status=401)) == 4
    assert cli_plumbing.exit_code(APIError("http_error", "x", status=302)) == 4  # a sign-in page
    assert cli_plumbing.exit_code(APIError("not_found", "x", status=404)) == 5
    assert run(capsys, "task", "lsit")[0] == cli_plumbing.EXIT_USAGE
    assert run(capsys)[0] == cli_plumbing.EXIT_USAGE
    assert run(capsys, "--help")[0] == 0
    monkeypatch.setenv("HUB_API_URL", "https://acme.tico.team")
    monkeypatch.setenv("HUB_TOKEN", "t")
    api.answers["me"] = APIError("not_found", "Task not found", status=404)
    code, out, _ = run(capsys, "whoami")
    assert code == 5 and json.loads(out)["error"] == "not_found"


# ----------------------------------------------------------------------------- tico events tail
class Stream(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class Dropped(Stream):
    """A stream whose connection breaks after its bytes."""

    def readline(self, *a):
        line = super().readline(*a)
        if not line:
            raise ConnectionResetError("dropped")
        return line


def sse(*events):
    out = "retry: 1000\n\n"
    for seq, topic, data in events:
        out += (f"id: {seq}\n" if seq is not None else "") + f"event: {topic}\ndata: {json.dumps(data)}\n\n"
    return out


class Tail:
    def __init__(self, streams, gets=None):
        self.streams, self.opened, self.gets, self.timeouts = list(streams), [], gets or {}, []

    def stream(self, path, query=None, last_event_id=None, timeout=60):
        self.opened.append((path, dict(query)))
        self.timeouts.append(timeout)
        nxt = self.streams.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    def get(self, path, /, **query):
        return self.gets[path]


def task_event(seq, task_id, pipeline, stage):
    return (
        seq,
        "tasks",
        {
            "seq": seq,
            "id": task_id,
            "kind": "update",
            "task": {"id": task_id, "pipeline_id": pipeline, "stage_id": stage},
        },
    )


def test_tail_prints_one_line_per_event_and_reconnects_from_the_last_change():
    first = sse((None, "ready", {"seq": 10, "after": 10, "topics": ["tasks"]}), task_event(11, "t1", "eng", "build"))
    second = sse(
        (None, "ready", {"seq": 12, "after": 11}), (13, "cursor", {"seq": 13}), task_event(14, "t1", "eng", "merge")
    )
    client = Tail([Dropped(first.encode()), APIError("unavailable", "down", retryable=True), Stream(second.encode())])
    lines, notes = [], []
    printed = events_tail.tail(client, count=2, emit=lines.append, note=notes.append, sleep=lambda s: None)
    assert printed == 2
    assert [json.loads(line)["task"]["stage_id"] for line in lines] == ["build", "merge"]
    assert json.loads(lines[0])["event"] == "tasks" and json.loads(lines[0])["seq"] == 11
    assert [q.get("after") for _, q in client.opened] == [None, 11, 11]  # resumed where the first one stopped
    assert any("dropped" in n for n in notes) and any("unavailable" in n for n in notes)


def test_tail_filters_by_task_on_the_server_and_by_pipeline_here():
    full = "60e069fc-af0b-4624-8a4e-d1ce8c4d10da"
    body = sse(
        task_event(1, "a", "eng", "build"),
        task_event(2, "b", "general", "todo"),
        task_event(3, "a", "general", "todo"),  # moved out of the pipeline: its last line still shows
        (4, "tasks", {"seq": 4, "id": "a", "gone": True}),
        (5, "tasks", {"seq": 5, "id": "c", "gone": True}),
        (6, "reset", {"seq": 6}),
    )
    client = Tail(
        [Stream(body.encode())],
        gets={
            "tasks/60e069fc": {"task": {"id": full}},
            "pipelines": {"pipelines": [{"id": "eng", "name": "Engineering"}]},
        },
    )
    lines = []
    events_tail.tail(
        client, tasks=["60e069fc"], pipelines=["engineering"], count=4, emit=lines.append, note=lambda t: None
    )
    assert client.opened[0][1] == {"topics": "tasks", "task": full, "after": None}
    assert [(json.loads(x)["event"], json.loads(x).get("id")) for x in lines] == [
        ("tasks", "a"),
        ("tasks", "a"),
        ("tasks", "a"),
        ("reset", None),
    ]


def test_tail_stops_on_an_expired_sign_in_or_a_refusal(shell, capsys):
    client = Tail([Stream(sse((None, "expired", {})).encode())])
    with pytest.raises(events_tail.SignInExpired):
        events_tail.tail(client, emit=lambda line: None, note=lambda t: None)
    args = hubcli.parser().parse_args(["events", "tail", "--topics", "bots"])
    assert cli_plumbing.events_tail(Tail([Stream(sse((None, "expired", {})).encode())]), args) == 4
    refused = Tail([APIError("forbidden", "Live events are for people's pages and agents", status=403)])
    assert cli_plumbing.events_tail(refused, args) == 2
    assert refused.opened[0][1]["topics"] == "bots"
    assert "Live events are for people" in capsys.readouterr().out


def test_tail_ends_at_its_timeout():
    now = [0]
    client = Tail([Stream(sse(task_event(1, "a", "eng", "x"), task_event(2, "a", "eng", "y")).encode())])
    lines = []

    def emit(line):
        lines.append(line)
        now[0] += 6  # 6 s after the first line, 12 s (past the 10 s timeout) after the second

    events_tail.tail(client, timeout=10, emit=emit, note=lambda t: None, clock=lambda: now[0])
    assert len(lines) == 2 and len(client.opened) == 1


class Quiet(Stream):
    """A healthy stream that only says ready, moves the cursor and sends keepalives; each line takes a second."""

    def __init__(self, body, now):
        super().__init__(body)
        self.now = now

    def readline(self, *a):
        self.now[0] += 1
        return super().readline(*a)


def test_quiet_tail_ends_at_its_timeout():
    now, slept = [0], []
    body = sse((None, "ready", {"seq": 1, "topics": ["tasks"]}), (2, "cursor", {"seq": 2})) + ": keepalive\n\n" * 20
    client = Tail([Quiet(body.encode(), now), APIError("unavailable", "down", retryable=True)])
    printed = events_tail.tail(
        client, timeout=3, emit=lambda line: None, note=lambda t: None, clock=lambda: now[0], sleep=slept.append
    )
    assert printed == 0
    assert client.timeouts == [3]  # a read never waits past the requested timeout
    assert now[0] <= 4 and len(client.opened) == 1  # stops at the deadline, not when the stream ends

    now[0], slept = 0, []
    client = Tail([APIError("unavailable", "down", retryable=True)])
    events_tail.tail(
        client,
        timeout=0.5,
        emit=lambda line: None,
        note=lambda t: None,
        clock=lambda: now[0],
        sleep=lambda s: (slept.append(s), now.__setitem__(0, now[0] + s)),
    )
    assert slept and max(slept) <= 0.5  # a reconnect wait stops at the deadline too

    class SlowConnect(Tail):  # the connect itself uses up the budget
        def stream(self, *a, **k):
            now[0] += 1
            return super().stream(*a, **k)

    now[0], notes = 0, []
    client = SlowConnect([APIError("unavailable", "timed out", retryable=True)])
    events_tail.tail(client, timeout=0.5, emit=lambda line: None, note=notes.append, clock=lambda: now[0])
    assert notes == [] and len(client.opened) == 1  # no "trying again" note when time is up


def test_events_tail_fields_pick_each_line(shell, capsys):
    args = hubcli.parser().parse_args(["events", "tail", "--count", "1"])
    client = Tail([Stream(sse(task_event(7, "a", "eng", "merge")).encode())])
    assert cli_plumbing.events_tail(client, args, fields=["seq", "task.stage_id"]) == 0
    assert json.loads(capsys.readouterr().out) == {"seq": 7, "task.stage_id": "merge"}


def test_profiles_file_is_not_needed_for_anything_else(shell, api, capsys, monkeypatch):
    profiles.config_path().parent.mkdir(parents=True)
    profiles.config_path().write_text("not json")
    monkeypatch.setenv("HUB_API_URL", "https://acme.tico.team")
    monkeypatch.setenv("HUB_TOKEN", "t")
    assert run(capsys, "whoami")[0] == 0  # a broken file matters only when a profile is asked for
    assert os.environ["HUB_API_URL"] == "https://acme.tico.team"
