"""Library › Skills as one list (docs/skills.md#function): the directory's skills and the ones Tico ships in its catalog,
each with its job function from SKILL.md (`metadata.function`), filtered by function and searched by words, through the
API and `tico skill list`. A skill Tico ships shows its roles, bots and files, and its usage is the bots' daily counts.

Ana owns the company, Ben is an admin, Cara is a member (the base fixture). The catalog is the real templates/catalog.
"""

import importlib.util
from datetime import datetime, timezone
from pathlib import Path

from backend import skill_catalog as SC
from backend import skill_suggestions as SS
from backend import bot_settings as BS
from backend.store import H
from backend.tests.helpers.api import get, restrict  # noqa: F401  (fixture)
from backend.tests.helpers.automation_cli import Client
from backend.tests.helpers.botops_parity import bot_id
from backend.tests.helpers.skills import SKILL, bundle, publish
from clients import hubcli, remotecli
from clients import skills as bundles

SHIPPED = "answer-a-customer"  # templates/catalog/_shared/.claude/skills, function: customers


def tagged(name, function=None, description="Sorts the inbox by urgency"):
    meta = f"metadata:\n  function: {function}\n" if function else ""
    return bundle(f"---\nname: {name}\ndescription: {description}\n{meta}---\n\nDo it.\n")


def row(rows, name):
    return next(r for r in rows if r["name"] == name)


def test_the_function_is_read_from_the_frontmatter_metadata():
    text = '---\nname: x\ndescription: d\nmetadata:\n  roles: [a, b]\n  function: "legal"\nother: 1\n---\nbody\n'
    assert bundles.metadata(text) == {"roles": ["a", "b"], "function": "legal"}
    assert bundles.function_of([{"path": "SKILL.md", "content": text}]) == "legal"
    listed = "---\nmetadata:\n  function: [finance, legal]\n---\n"
    assert bundles.function_of([{"path": "SKILL.md", "content": listed}]) == "finance"
    # None at all, one outside metadata, or a block never closed: no function, never a guess.
    for text in (
        "---\nname: x\n---\n",
        "---\nfunction: legal\n---\n",
        "---\nmetadata:\n  function: legal\n",
        "no frontmatter",
    ):
        assert bundles.function_of([{"path": "SKILL.md", "content": text}]) is None, text
    # Only the keys directly under a top-level metadata: a nested map's own `function` is not one.
    nested = "---\nmetadata:\n  owner:\n    function: legal\n  function: sales\nother:\n  function: data\n---\n"
    assert bundles.metadata(nested) == {"owner": "", "function": "sales"}
    deep = "---\nmetadata:\n  owner:\n    function: legal\n---\n"
    assert bundles.function_of([{"path": "SKILL.md", "content": deep}]) is None
    assert [bundles.function_group(v) for v in ("Legal", " finance ", "adtech", None, "")] == [
        "legal",
        "finance",
        "other",
        "other",
        "other",
    ]


def test_every_skill_tico_ships_is_listed_once_with_its_function(api):
    publish(api, name="triage", files=tagged("triage", "operations"))
    body = get(api, "skills")
    names = [r["name"] for r in body["skills"]]
    assert names == sorted(names) and len(names) == len(set(names))
    shipped = row(body["skills"], SHIPPED)
    assert shipped["source"] == "tico" and shipped["function"] == "customers" and shipped["latest"] is None
    assert shipped["description"] and shipped["id"] is None
    assert len(body["skills"]) == len(SC.shipped()) + 1
    mine = row(body["skills"], "triage")
    assert mine["source"] == "directory" and mine["function"] == "operations" and mine["latest"]["number"] == 1
    # The counts per function cover every row, in the menu's order, Other last.
    counts = {c["function"]: c["count"] for c in body["functions"]}
    assert sum(counts.values()) == len(body["skills"]) and counts["customers"] >= 1
    order = [c["function"] for c in body["functions"]]
    assert order == sorted(order, key=lambda f: (bundles.FUNCTIONS + ("other",)).index(f))


def test_the_list_keeps_one_function_and_the_skills_with_every_search_word(api):
    publish(
        api, name="contract-review", files=tagged("contract-review", "Legal", "Reads a zebrawood supplier contract")
    )
    publish(api, name="ad-check", files=tagged("ad-check", "adtech"))
    publish(api, name="triage", files=tagged("triage"))

    legal = get(api, "skills?function=legal")["skills"]
    assert "contract-review" in [r["name"] for r in legal] and len(legal) > 1  # Tico ships legal skills too
    assert {bundles.function_group(r["function"]) for r in legal} == {"legal"}

    other = [r["name"] for r in get(api, "skills?function=other")["skills"]]
    assert {"ad-check", "triage"} <= set(other) and SHIPPED not in other  # an unknown value and none are Other
    assert [r["name"] for r in get(api, "skills?function=adtech")["skills"]] == ["ad-check"]  # an unknown one, exactly

    found = get(api, "skills?q=zebrawood%20CONTRACT")
    assert [r["name"] for r in found["skills"]] == ["contract-review"]
    assert found["functions"] == [{"function": "legal", "count": 1}]  # the counts follow the search
    by_function = get(api, "skills?q=customers&function=customers")["skills"]
    assert SHIPPED in [r["name"] for r in by_function]  # the search reads the function too
    assert get(api, "skills?q=" + "x" * 201, expected=422)


def test_each_approval_keeps_the_function_its_skill_md_names(api):
    publish(api, name="triage", files=tagged("triage", "operations"))
    with api.app.state.store.read() as c:
        assert c.execute("SELECT function FROM skills WHERE name='triage'").fetchone()[0] == "operations"
    publish(api, name="triage", files=tagged("triage", "finance", "Sorts the inbox by urgency, then by sender"))
    publish(api, bundle(SKILL.replace("urgency", "urgency first")))  # a version with none: never refused, Other
    with api.app.state.store.read() as c:
        assert c.execute("SELECT function FROM skills WHERE name='triage'").fetchone()[0] is None
    assert row(get(api, "skills?function=other")["skills"], "triage")["function"] is None


def test_the_migration_fills_the_function_of_skills_approved_before_it(api):
    publish(api, name="triage", files=tagged("triage", "legal"))
    publish(api, name="ad-check", files=tagged("ad-check", "sales"))
    nested = "---\nname: deep\ndescription: d\nmetadata:\n  owner:\n    function: legal\n---\n\nDo it.\n"
    publish(api, name="deep", files=bundle(nested))
    path = next(Path("backend/migrations").glob("*_skill_function.py"))
    spec = importlib.util.spec_from_file_location("skill_function", path)
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    migration.BATCH = 1  # one skill a batch: every batch is read
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE skills SET function=NULL")
        migration.up(c)
        migration.up(c)  # again: nothing changes
        found = dict(c.execute("SELECT name, function FROM skills").fetchall())
    assert found == {"triage": "legal", "ad-check": "sales", "deep": None}


def test_a_shipped_skill_counts_only_the_provisioned_bots_the_caller_may_see(api):
    ops, finance = bot_id(api, "ops"), bot_id(api, "finance")
    with api.app.state.store.transaction() as c:
        BS.update(c, ops, {"template": "support-owner"})
        BS.update(c, finance, {"template": "support-owner"})
        restrict(c, ops, see={"people": ["ana"]}, read={"people": ["ana"]}, write={"people": ["ana"]})
    for token, bots in (("ana-test", [ops, finance]), ("cara-test", [finance])):
        listed = row(get(api, "skills", token)["skills"], SHIPPED)
        assert {t["id"] for t in listed["on"]} == set(bots) and listed["on_count"] == len(bots), token
        shipped = get(api, "skills/" + SHIPPED, token)["shipped"]
        assert {b["id"] for b in shipped["bots"]} == set(bots) and shipped["bots_count"] == len(bots), token
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bots SET state='planned' WHERE id=?", (finance,))  # not made yet: has nothing
    assert row(get(api, "skills", "cara-test")["skills"], SHIPPED)["on_count"] == 0
    assert [b["id"] for b in get(api, "skills/" + SHIPPED, "ana-test")["shipped"]["bots"]] == [ops]


def test_a_skill_past_the_lists_first_rows_is_still_found_and_filtered(api):
    now = H.now()
    with api.app.state.store.transaction() as c:
        c.executemany(
            "INSERT INTO skills (id, name, description, function, created_by_type, created_by_id, created_at, updated_at) "
            "VALUES (?,?,?,?,'Human','ana',?,?)",
            [(f"sk_bulk{i:04}", f"bulk-{i:04}", "Filler", "data", now, now) for i in range(520)]
            + [("sk_zz", "zz-needle", "The last one", "legal", now, now)],
        )
    found = get(api, "skills?q=needle")["skills"]
    assert [r["name"] for r in found] == ["zz-needle"] and found[0]["latest"] is None
    assert "zz-needle" in [r["name"] for r in get(api, "skills?function=legal")["skills"]]
    data = get(api, "skills?function=data")
    assert {c["function"]: c["count"] for c in data["functions"]}["data"] >= 520
    assert len([r for r in data["skills"] if r["source"] == "directory"]) == 500  # the details stop at the cap


def _catalog(tmp_path):
    """A catalog of two roles that ship `good` in different copies, a skill folder that is a link out of it, and a
    file that is one."""
    root, outside = tmp_path / "catalog", tmp_path / "outside"
    (outside / "evil").mkdir(parents=True)
    (outside / "evil" / "SKILL.md").write_text("---\nname: evil\ndescription: Out\n---\n")
    (outside / "secret.md").write_text("secret")
    for template, words in (("legal", "Legal copy"), ("ops", "Ops copy")):
        (root / template / "skills" / "good").mkdir(parents=True)
        (root / template / "card.yaml").write_text(f"name: {template.title()}\n")
        (root / template / "skills" / "good" / "SKILL.md").write_text(f"---\nname: good\ndescription: {words}\n---\n")
    (root / "legal" / "skills" / "evil").symlink_to(outside / "evil")
    (root / "legal" / "skills" / "good" / "secret.md").symlink_to(outside / "secret.md")
    (root / "ops" / ".claude").mkdir()
    (root / "ops" / ".claude" / "skills").symlink_to(outside)  # a parent folder that leaves the catalog
    return root


def test_nothing_outside_the_catalog_is_indexed_or_served(tmp_path):
    root = _catalog(tmp_path)
    SC.forget()
    try:
        shipped = SC.shipped(root)
        assert sorted(shipped) == ["good"] and shipped["good"]["templates"] == ["legal", "ops"]
        good = shipped["good"]
        assert [f["path"] for f in SC.files(good, directory=root)] == ["SKILL.md"]
        assert "Legal copy" in SC.files(good, directory=root)[0]["content"]  # the first role's, by name
        # A role with bots here is the copy its page shows.
        assert "Ops copy" in SC.files(good, with_bots={"ops"}, directory=root)[0]["content"]
        assert SC.ships(root) == {"legal": frozenset({"good"}), "ops": frozenset({"good"})}
    finally:
        SC.forget()


def test_a_skill_tico_ships_shows_its_roles_bots_and_files(api):
    ops = bot_id(api, "ops")
    with api.app.state.store.transaction() as c:
        BS.update(c, ops, {"template": "support-owner"})
    body = get(api, "skills/" + SHIPPED)
    assert body["skill"]["source"] == "tico" and body["skill"]["function"] == "customers"
    assert body["versions"] == [] and body["installs"] == []
    shipped = body["shipped"]
    assert "support-owner" in [r["template"] for r in shipped["roles"]]
    assert [b["id"] for b in shipped["bots"]] == [ops] and shipped["bots_count"] == 1
    assert shipped["files"][0]["path"] == "SKILL.md" and "function: customers" in shipped["files"][0]["content"]
    listed = row(get(api, "skills")["skills"], SHIPPED)
    assert [t["id"] for t in listed["on"]] == [ops] and listed["on_count"] == 1
    get(api, "skills/no-such-skill", expected=404)


def _used(api, bot, name, uses, day=None):
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO unlisted_skill_usage_dailies (used_on, name, bot_id, uses_count) VALUES (?,?,?,?)",
            (day or H.now()[:10], name, bot, uses),
        )


def test_a_shipped_skills_usage_is_the_bots_daily_counts_and_it_is_not_in_directory(api):
    ops = bot_id(api, "ops")
    with api.app.state.store.transaction() as c:
        BS.update(c, ops, {"template": "support-owner"})
    _used(api, ops, SHIPPED, 12)
    _used(api, ops, "warranty-lookup", 11)
    usage = get(api, "skills/usage?days=30", "ben-test")
    used = row(usage["skills"], SHIPPED)
    assert used["uses"] == 12 and used["bots"] == 1 and used["people"] is None and used["by_trigger"] is None
    assert [r["name"] for r in usage["popular_elsewhere"]] == ["warranty-lookup"]
    detail = get(api, f"skills/{SHIPPED}/usage?days=30", "ben-test")
    assert detail["source"] == "tico" and detail["totals"]["uses"] == 12
    assert detail["totals"]["by_trigger"] is None and detail["totals"]["people"] is None
    assert [a["id"] for a in detail["actors"]] == [ops]
    # Asking a bot to submit a skill Tico already ships would be noise: only the other one is suggested.
    with api.app.state.store.read() as c:
        found = SS.candidates(c, datetime.now(timezone.utc))
    assert ("promote", "warranty-lookup") in found and ("promote", SHIPPED) not in found


def test_a_bots_own_skill_with_a_shipped_name_stays_its_own(api):
    """Only a bot whose role ships the name used the shipped skill: another bot's skill of that name is its own, not in
    the directory and one to ask for, as before."""
    ops, finance = bot_id(api, "ops"), bot_id(api, "finance")
    with api.app.state.store.transaction() as c:
        BS.update(c, ops, {"template": "support-owner"})
    _used(api, ops, SHIPPED, 3)
    _used(api, finance, SHIPPED, 20)
    usage = get(api, "skills/usage?days=30", "ben-test")
    used = row(usage["skills"], SHIPPED)
    assert used["uses"] == 3 and used["bots"] == 1
    elsewhere = row(usage["popular_elsewhere"], SHIPPED)
    assert elsewhere["uses"] == 20 and [b["id"] for b in elsewhere["bots"]] == [finance]
    detail = get(api, f"skills/{SHIPPED}/usage?days=30", "ben-test")
    assert detail["totals"]["uses"] == 3 and [a["id"] for a in detail["actors"]] == [ops]
    with api.app.state.store.read() as c:
        found = SS.candidates(c, datetime.now(timezone.utc))
    assert found[("promote", SHIPPED)]["target_id"] == finance and found[("promote", SHIPPED)]["uses_count"] == 20


def test_approving_a_shipped_skill_into_the_directory_keeps_its_last_use(api):
    ops = bot_id(api, "ops")
    with api.app.state.store.transaction() as c:
        BS.update(c, ops, {"template": "support-owner"})
    _used(api, ops, SHIPPED, 4, "2026-01-02")
    publish(api, name=SHIPPED, files=tagged(SHIPPED, "customers", "Answers a customer"))
    usage = get(api, "skills/usage?days=30", "ben-test")
    assert [r["name"] for r in usage["skills"]].count(SHIPPED) == 1
    assert row(usage["skills"], SHIPPED)["last_used_at"] == "2026-01-02"
    assert get(api, f"skills/{SHIPPED}/usage?days=30", "ben-test")["totals"]["last_used_at"] == "2026-01-02"


def test_tico_skill_list_filters_by_function_and_words(api):
    publish(
        api, name="contract-review", files=tagged("contract-review", "legal", "Reads a zebrawood supplier contract")
    )
    args = hubcli.parser().parse_args(["skill", "list", "zebrawood", "contract", "--function", "legal"])
    assert args.fn == "skill list" and args.q == ["zebrawood", "contract"] and args.function == "legal"
    found = remotecli.via_tool(Client(api, "ana-test"), args)
    assert [r["name"] for r in found["skills"]] == ["contract-review"]
    everything = remotecli.via_tool(Client(api, "cara-test"), hubcli.parser().parse_args(["skill", "list"]))
    assert len(everything["skills"]) == len(SC.shipped()) + 1
