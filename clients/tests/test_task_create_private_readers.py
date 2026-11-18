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
