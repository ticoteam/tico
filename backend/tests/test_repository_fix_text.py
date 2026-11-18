from backend import bot_settings as BS
from backend.health import repository_fix


def test_a_checkout_without_its_instructions_is_not_told_to_clone_over_itself(api):
    """#647: a computer that holds a bot's checkout without AGENTS.md cannot take `gh repo clone` into that folder
    (git refuses a folder that is not empty). Health says the runner re-clones it by itself and, for an older
    runner, to set the folder aside first; a computer with no checkout at all still gets the plain clone."""
    with api.app.state.store.transaction() as c:
        BS.write(c, "cpo", {"name": "cpo", "repo": "bot-cpo"})
        _, missing = repository_fix(c, "cpo", "Ana Mac", "org", "Missing bot repository")
        _, broken = repository_fix(
            c,
            "cpo",
            "Ana Mac",
            "org",
            "org/bot-cpo is checked out here but has no AGENTS.md: it has no upstream branch to fast-forward from",
        )
    assert missing.startswith("Run `gh repo clone org/bot-cpo ") and "set-aside" not in missing, missing
    assert "clones it again by itself" in broken, broken
    assert "`mv " in broken and ".set-aside" in broken and "gh repo clone org/bot-cpo" in broken, broken
    assert broken.index("`mv ") < broken.index("gh repo clone"), "the folder is set aside before the clone"
