"""The old conversation and message spellings of `hub` still run the commands the threads build keeps.

clients/hubcli.py RENAMED maps `hub history`, `hub say` and `hub ack` onto `conversation show`, `message send` and
`message mark-read`, and a bot's prompt names `--before` and `--since` on `conversation show`. test_hubcli.py covers
only the `notice` rename.
"""

from clients import hubcli


def renamed(*argv):
    new, notice = hubcli.rename_argv(list(argv))
    assert notice is not None, argv
    return new


def test_the_old_conversation_and_message_spellings_map_onto_the_new_commands():
    assert renamed("history", "c1") == ["conversation", "show", "c1"]
    assert renamed("history", "c1", "--before", "m1") == ["conversation", "show", "c1", "--before", "m1"]
    assert renamed("say", "ops", "Hello") == ["message", "send", "ops", "Hello"]
    assert renamed("ack", "m1") == ["message", "mark-read", "m1"]
    assert hubcli.rename_argv(["conversation", "show", "c1"]) == (["conversation", "show", "c1"], None)


def test_conversation_show_takes_the_paging_flags_the_runner_prompt_names():
    args = hubcli.parser().parse_args(["conversation", "show", "c1", "--before", "m1", "--since", "2h"])
    assert (args.fn, args.conversation, args.before, args.since) == ("conversation show", "c1", "m1", "2h")
