"""The bulk change's dry run says what it does not count: the org rule check and the handoff check run only in a real run,
so a task one of them would hold shows as changed in a dry run. The tool text and the CLI help name that gap, so a bot
does not trust a dry run's counts as the real ones."""

import argparse

from clients import hubcli, hubtools


def _bulk_tool():
    return next(t for t in hubtools.TOOLS if t["name"] == "hub_task_bulk")


def test_the_bulk_tool_text_names_the_rule_and_handoff_gap_of_a_dry_run():
    tool = _bulk_tool()
    text = tool["description"]
    assert "Run `dry_run` first" in text
    assert "skips the org rule check and the handoff check" in text
    assert "counts as changed there and is skipped for real" in text
    # The old claim that a dry run counts the same as the real run must not come back.
    assert "same counts" not in text and "(counts only)" not in text


def test_the_bulk_dry_run_field_says_rule_and_handoff_holds_are_not_counted():
    field = _bulk_tool()["inputSchema"]["properties"]["dry_run"]
    assert field["description"].startswith("Count only.")
    assert "Does not count rule or handoff holds" in field["description"]
    assert "shows as changed here" in field["description"]


def _subcommands(parser):
    return next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction)).choices


def test_the_cli_dry_run_help_names_the_same_gap():
    bulk = _subcommands(_subcommands(hubcli.parser())["task"])["bulk"]
    help_text = next(a for a in bulk._actions if a.dest == "dry_run").help
    assert "not rule or handoff holds" in help_text
