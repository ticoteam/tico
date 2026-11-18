"""`tico task create` tells a bot filing a private task who else reads it as it moves, first and apart from the JSON a
script parses (Product #449, backend/private_readers.py)."""

import json

from clients import hubcli, remotecli

LINE = "Private, but these can read it as it moves through Engineering: Builder or Builder 2."


def test_the_filer_reads_who_else_reads_it_and_the_task_json_is_unchanged(monkeypatch, capsys):
    made = {"task": {"id": "t1", "title": "Fix it"}, "private_readers": LINE}
    monkeypatch.setattr(remotecli, "run", lambda args, who=None: made)
    args = hubcli.parser().parse_args(
        ["task", "create", "--title", "Fix it", "--body", "Rounds pay.", "--pipeline", "engineering"]
    )
    assert remotecli.main(args) == 0
    out = capsys.readouterr()
    assert out.err.splitlines()[0] == "tico: " + LINE
    assert json.loads(out.out)["task"]["id"] == "t1"


def test_a_task_nobody_else_reads_prints_no_line(monkeypatch, capsys):
    monkeypatch.setattr(remotecli, "run", lambda args, who=None: {"task": {"id": "t2"}})
    args = hubcli.parser().parse_args(["task", "create", "--title", "Fix it", "--body", "Rounds pay."])
    remotecli.main(args)
    assert "tico:" not in capsys.readouterr().err


def _dry(monkeypatch, said, *extra):
    """`tico task create --dry-run` as a bot on the cloud API; `said` is the server dry run's answer, or an error."""
    from clients import tico

    monkeypatch.setenv("HUB_API_URL", "https://acme.example")
    monkeypatch.setenv("HUB_BOT", "builder")
    asked = []

    def post(self, path, body=None, key=None):
        asked.append((path, body))
        if isinstance(said, Exception):
            raise said
        return said

    monkeypatch.setattr(tico.Client, "post", post)
    args = hubcli.parser().parse_args(
        ["task", "create", "--title", "Fix it", "--body", "Rounds pay.", "--owner", "bot:builder", *extra, "--dry-run"]
    )
    return hubcli.cmd_task_dry_run(args, None), asked


def test_the_dry_run_into_a_pipeline_says_who_else_reads_it(monkeypatch, capsys):
    """Validate found the command's dry run checked only here and never said the line: it asks the server's dry run,
    which files nothing."""
    code, asked = _dry(monkeypatch, {"ok": True, "private_readers": LINE}, "--private", "--pipeline", "engineering")
    assert code == 0
    assert capsys.readouterr().err.splitlines()[0] == "tico: " + LINE
    assert asked == [
        (
            "tasks/dry-run",
            {
                "title": "Fix it",
                "body": "Rounds pay.",
                "owner": "bot:builder",
                "pipeline": "engineering",
                "private": True,
            },
        )
    ]


def test_the_dry_run_answers_alone_when_the_server_cannot_be_asked_or_no_pipeline_is_named(monkeypatch, capsys):
    code, _ = _dry(monkeypatch, OSError("offline"), "--private", "--pipeline", "engineering")
    assert code == 0 and "tico:" not in capsys.readouterr().err
    code, asked = _dry(monkeypatch, {"ok": True, "private_readers": LINE}, "--private")
    assert code == 0 and asked == [] and "tico:" not in capsys.readouterr().err
