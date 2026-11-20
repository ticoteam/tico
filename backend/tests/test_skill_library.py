"""Tico's library skills in each company's directory (backend/skill_library.py, docs/skills.md#ticos-library): seeded at
start as approved skills marked Tico's, installable, with nothing installed; a rerun does nothing, a changed file is the
next version, a skill the library drops is retired, and the company's own skill of a name is left alone.

Ana owns the company, Ben is an admin, Cara is a member (the base fixture). The API tests seed the real
templates/catalog; the others a small one of their own.
"""

import json
import logging

from backend import bot_settings as BS
from backend import skill_catalog as SC
from backend import skill_library as SL
from backend.store import H
from backend.tests.helpers.api import get, post  # noqa: F401  (fixture)
from backend.tests.helpers.automation_cli import Client
from backend.tests.helpers.botops_parity import bot_id
from backend.tests.helpers.skills import publish
from clients import hubcli, remotecli

SHIPPED = "answer-a-customer"  # in the library, function customers; the support-owner role ships it
NAMES = {"company_name": "Acme", "app_name": "Acme Hub", "assistant_name": "Assistant"}


def _write(root, name, body="Do it.", function="legal", extra=None):
    folder = root / "_shared" / ".claude" / "skills" / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: The {name} skill\nmetadata:\n  function: {function}\n---\n\n{body}\n"
    )
    for path, text in (extra or {}).items():
        (folder / path).parent.mkdir(parents=True, exist_ok=True)
        (folder / path).write_text(text)
    return folder


def _seed(store, root, names=NAMES):
    SL.forget()
    with store.transaction() as c:
        return SL.seed(c, names, root)


def _rows(store):
    with store.read() as c:
        return {
            r["name"]: dict(r)
            for r in c.execute(
                "SELECT s.name, s.created_by_type, s.created_by_id, s.deleted_at, s.function, v.number, v.sha256, "
                "v.approved_by_id, v.proposal_id, (SELECT COUNT(*) FROM skill_versions x WHERE x.skill_id=s.id) AS versions "
                "FROM skills s LEFT JOIN skill_versions v ON v.id=s.latest_version_id"
            )
        }


def test_a_fresh_company_gets_the_library_approved_as_ticos_and_a_rerun_does_nothing(company, tmp_path):
    root = tmp_path / "catalog"
    _write(root, "review-a-contract", extra={"references/clauses.md": "Indemnity.\n"})
    _write(root, "check-the-build", "Ask {{app_name}} for the checks.", function="engineering")
    done = _seed(company, root)
    assert sorted(done["added"]) == ["check-the-build", "review-a-contract"] and not done["skipped"]
    rows = _rows(company)
    for row in rows.values():
        assert row["created_by_type"] == "Tico" and row["created_by_id"] == H.TICO_ACTOR and row["number"] == 1
        assert row["approved_by_id"] is None and row["proposal_id"] is None and row["deleted_at"] is None
    assert rows["review-a-contract"]["function"] == "legal" and rows["check-the-build"]["function"] == "engineering"
    with company.read() as c:
        files = json.loads(
            c.execute(
                "SELECT files FROM skill_versions WHERE sha256=?", (rows["check-the-build"]["sha256"],)
            ).fetchone()[0]
        )
        assert "Ask Acme Hub for the checks." in files[0]["content"]  # the company's names, as a bot's copy has them
        paths = json.loads(
            c.execute(
                "SELECT files FROM skill_versions WHERE sha256=?", (rows["review-a-contract"]["sha256"],)
            ).fetchone()[0]
        )
        assert [f["path"] for f in paths] == ["SKILL.md", "references/clauses.md"]
        # Approved without a proposal or a screen, and nothing installed or staged anywhere.
        for table in ("skill_installs", "skill_receipts", "skill_proposals", "skill_screenings"):
            assert c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0, table
    assert _seed(company, root) == {"unchanged": True}
    assert _rows(company) == rows
    # The company's names are part of what it gets: a new app name is the next version of the skill that says it.
    assert _seed(company, root, {**NAMES, "app_name": "Other"})["updated"] == ["check-the-build"]


def test_a_changed_file_is_the_next_version_and_an_earlier_bundle_is_latest_again(company, tmp_path):
    root = tmp_path / "catalog"
    _write(root, "review-a-contract")
    _seed(company, root)
    first = _rows(company)["review-a-contract"]
    _write(root, "review-a-contract", "Do it, then list the clauses.")
    assert _seed(company, root)["updated"] == ["review-a-contract"]
    second = _rows(company)["review-a-contract"]
    assert second["number"] == 2 and second["versions"] == 2 and second["sha256"] != first["sha256"]
    _write(root, "review-a-contract")  # the release before's text again: that version, not a third
    assert _seed(company, root)["updated"] == ["review-a-contract"]
    again = _rows(company)["review-a-contract"]
    assert again["number"] == 1 and again["versions"] == 2 and again["sha256"] == first["sha256"]


def test_a_skill_the_library_drops_is_retired_unless_a_person_approved_its_latest(company, tmp_path):
    root = tmp_path / "catalog"
    for name in ("old-skill", "kept-skill", "stays"):
        _write(root, name)
    _seed(company, root)
    with company.transaction() as c:  # a person's version on top of Tico's: the company's now
        skill = c.execute("SELECT id FROM skills WHERE name='kept-skill'").fetchone()[0]
        c.execute(
            "INSERT INTO skill_versions (id, skill_id, number, sha256, files, description, proposal_id, approved_by_id, "
            "approved_at, created_at) VALUES ('skv_mine', ?, 2, 'f00', '[]', '', 'skp_mine', 'ben', ?, ?)",
            (skill, H.now(), H.now()),
        )
        c.execute("UPDATE skills SET latest_version_id='skv_mine' WHERE id=?", (skill,))
    for name in ("old-skill", "kept-skill"):
        (root / "_shared" / ".claude" / "skills" / name / "SKILL.md").unlink()
    done = _seed(company, root)
    assert done["retired"] == ["old-skill"] and not done["added"]
    rows = _rows(company)
    assert (
        rows["old-skill"]["deleted_at"]
        and rows["kept-skill"]["deleted_at"] is None
        and rows["stays"]["deleted_at"] is None
    )
    _write(root, "old-skill")  # back in the library: back in the directory, at the version it had
    assert _seed(company, root)["updated"] == ["old-skill"]
    back = _rows(company)["old-skill"]
    assert back["deleted_at"] is None and back["number"] == 1 and back["versions"] == 1


def test_the_companys_own_skill_of_a_library_name_is_left_alone_and_logged_once(company, tmp_path, caplog):
    root = tmp_path / "catalog"
    _write(root, "triage")
    _write(root, "review-a-contract")
    now = H.now()
    with company.transaction() as c:
        c.execute(
            "INSERT INTO skills (id, name, description, created_by_type, created_by_id, created_at, updated_at) "
            "VALUES ('sk_mine', 'triage', 'Ours', 'Human', 'ana', ?, ?)",
            (now, now),
        )
    with caplog.at_level(logging.INFO, logger="tico.skill_library"):
        done = _seed(company, root)
        assert done["skipped"] == ["triage"] and done["added"] == ["review-a-contract"]
        _write(root, "review-a-contract", "Changed.")
        assert _seed(company, root)["skipped"] == ["triage"]
    assert sum("triage" in r.getMessage() for r in caplog.records) == 1
    mine = _rows(company)["triage"]
    assert mine["created_by_type"] == "Human" and mine["versions"] == 0  # untouched


def test_a_missing_or_empty_library_retires_nothing_and_is_read_again(company, tmp_path, caplog):
    root = tmp_path / "catalog"
    _write(root, "review-a-contract")
    _seed(company, root)
    missing = tmp_path / "nowhere"
    with caplog.at_level(logging.WARNING, logger="tico.skill_library"):
        assert _seed(company, missing) == {"unreadable": True}
        empty = tmp_path / "empty"
        (empty / "_shared" / ".claude" / "skills" / "broken").mkdir(parents=True)  # no SKILL.md: nothing valid
        assert _seed(company, empty) == {"unreadable": True}
    assert any("nothing is seeded or retired" in r.getMessage() for r in caplog.records)
    assert _rows(company)["review-a-contract"]["deleted_at"] is None
    # Not kept: once the library is there again, it is read.
    with company.transaction() as c:
        assert SL.seed(c, NAMES, missing) == {"unreadable": True}
        _write(missing, "review-a-contract")
        assert SL.seed(c, NAMES, missing) == {"unchanged": True}  # the same library as seeded


def test_nothing_outside_the_real_catalog_is_seeded(company, tmp_path):
    root, outside = tmp_path / "catalog", tmp_path / "outside"
    _write(root, "review-a-contract")
    _write(outside, "evil")
    (root / "_shared" / ".claude" / "skills" / "evil").symlink_to(outside / "_shared" / ".claude" / "skills" / "evil")
    done = _seed(company, root)
    assert done["added"] == ["review-a-contract"] and "evil" not in _rows(company)
    # A library folder that is a link out of the catalog is no library at all.
    linked = tmp_path / "linked"
    (linked / "_shared" / ".claude").mkdir(parents=True)
    (linked / "_shared" / ".claude" / "skills").symlink_to(outside / "_shared" / ".claude" / "skills")
    assert _seed(company, linked) == {"unreadable": True}
    assert _rows(company)["review-a-contract"]["deleted_at"] is None


def test_a_skill_the_rules_refuse_is_kept_then_retired_when_it_leaves(company, tmp_path):
    root = tmp_path / "catalog"
    _write(root, "review-a-contract")
    _write(root, "check-the-build")
    _seed(company, root)
    broken = root / "_shared" / ".claude" / "skills" / "check-the-build" / "SKILL.md"
    broken.write_text("---\nname: another-name\ndescription: d\n---\n")  # pack refuses a name that differs
    done = _seed(company, root)
    assert not done["retired"] and _rows(company)["check-the-build"]["deleted_at"] is None
    broken.unlink()
    broken.parent.rmdir()
    assert _seed(company, root)["retired"] == ["check-the-build"]


def test_the_migration_lets_ticos_version_have_no_proposal_or_approver(company):
    with company.read() as c:
        info = {r[1]: r[3] for r in c.execute("PRAGMA table_info(skill_versions)")}
        assert info["approved_by_id"] == 0 and info["proposal_id"] == 0 and info["sha256"] == 1
        refs = {(r[3], r[2]) for r in c.execute("PRAGMA foreign_key_list(skill_versions)")}
        assert ("approved_by_id", "humans") in refs and (
            "skill_id",
            "skills",
        ) in refs  # a person's approval still checked


# ----------------------------------------------------------------------------- the real library, through the API
def row(rows, name):
    return next(r for r in rows if r["name"] == name)


def test_the_library_is_listed_once_per_name_installable_and_on_its_role_bots(api):
    store = api.app.state.store
    SL.forget()
    with store.transaction() as c:
        done = SL.seed_company(c, store.settings)
    library = sorted(SL.packed(store.settings.catalog_dir).skills)
    assert sorted(done["added"]) == library and len(library) >= 50
    with store.transaction() as c:
        assert SL.seed_company(c, store.settings) == {"unchanged": True}

    ops, finance = bot_id(api, "ops"), bot_id(api, "finance")
    with store.transaction() as c:
        BS.update(c, ops, {"template": "support-owner"})  # ships answer-a-customer as a file
    body = get(api, "skills")
    names = [r["name"] for r in body["skills"]]
    assert len(names) == len(set(names)) == len(SC.shipped())  # every library name is also shipped: one row each
    seeded = row(body["skills"], SHIPPED)
    assert seeded["source"] == "directory" and seeded["from_tico"] and seeded["latest"]["number"] == 1
    assert seeded["function"] == "customers" and seeded["installs_count"] == 0
    assert [t["id"] for t in seeded["on"]] == [ops] and seeded["on_count"] == 1  # its role's bot, through the catalog
    role_only = [r for r in body["skills"] if r["name"] not in library]
    assert role_only and all(r["source"] == "tico" and r["latest"] is None for r in role_only)  # listed, not seeded

    detail = get(api, "skills/" + SHIPPED)
    assert detail["skill"]["from_tico"] and detail["versions"][0]["approved_by_id"] is None
    assert detail["installs"] == [] and [b["id"] for b in detail["shipped"]["bots"]] == [ops]

    # Install works for an agent that lacks it. The one whose role ships it has it as a file: a second copy is refused
    # (a team's install leaves it out), and the picker does not offer it.
    refused = post(api, f"skills/{SHIPPED}/installs", {"target_type": "Bot", "target_id": ops}, expected=409)
    assert refused["error"]["code"] == "has_from_role"
    assert "already has answer-a-customer from its role" in str(refused["error"])
    both = [{"target_type": "Bot", "target_id": b} for b in (ops, finance)]
    done = post(api, f"skills/{SHIPPED}/installs/bulk", {"targets": both, "version_policy": "latest"})
    assert [i["target"]["id"] for i in done["installs"]] == [finance]
    assert done["left_out"] == [{"target_type": "Bot", "target_id": ops}]
    offered = {b["id"] for b in get(api, f"skills/install-targets?skill={SHIPPED}")["bots"]}
    assert finance in offered and ops not in offered
    assert ops in {b["id"] for b in get(api, "skills/install-targets")["bots"]}
    seeded = row(get(api, "skills")["skills"], SHIPPED)
    assert seeded["installs_count"] == 1 and seeded["on_count"] == 2
    assert sorted(t["id"] for t in seeded["on"]) == sorted([ops, finance])

    legal = [n for n in library if SL.bundles.function_group(SC.shipped()[n]["function"]) == "legal"]
    found = remotecli.via_tool(
        Client(api, "cara-test"), hubcli.parser().parse_args(["skill", "list", "--function", "legal"])
    )
    listed = {r["name"]: r for r in found["skills"]}
    assert legal and all(listed[n]["source"] == "directory" and listed[n]["latest"] for n in legal)


def test_a_role_bots_uses_before_the_library_was_seeded_still_count(api):
    store = api.app.state.store
    ops = bot_id(api, "ops")
    with store.transaction() as c:
        BS.update(c, ops, {"template": "support-owner"})
        c.execute(
            "INSERT INTO unlisted_skill_usage_dailies (used_on, name, bot_id, uses_count) VALUES (?,?,?,5)",
            (H.now()[:10], SHIPPED, ops),
        )
    SL.forget()
    with store.transaction() as c:
        SL.seed_company(c, store.settings)
    used = row(get(api, "skills/usage?days=30", "ben-test")["skills"], SHIPPED)
    assert used["uses"] == 5 and used["bots"] == 1 and used["by_trigger"]["model"] == 5
    detail = get(api, f"skills/{SHIPPED}/usage?days=30", "ben-test")
    assert detail["totals"]["uses"] == 5 and detail["totals"]["bots"] == 1
    assert [(a["id"], a["uses"], a["by_trigger"]["model"]) for a in detail["actors"]] == [(ops, 5, 5)]
    assert get(api, f"skills/{SHIPPED}/usage?days=30", "cara-test")["actors"] == []  # someone else's: totals only


def test_the_companys_own_skill_of_a_shipped_name_gets_no_role_bots_or_their_uses(api):
    store = api.app.state.store
    ops = bot_id(api, "ops")
    with store.transaction() as c:
        BS.update(c, ops, {"template": "support-owner"})
        c.execute(
            "INSERT INTO unlisted_skill_usage_dailies (used_on, name, bot_id, uses_count) VALUES (?,?,?,5)",
            (H.now()[:10], SHIPPED, ops),
        )
    publish(api, name=SHIPPED)  # the company's own, before any seed: seeding leaves it alone
    SL.forget()
    with store.transaction() as c:
        assert SHIPPED in SL.seed_company(c, store.settings)["skipped"]
    mine = row(get(api, "skills")["skills"], SHIPPED)
    assert not mine["from_tico"] and mine["on"] == [] and mine["on_count"] == 0
    assert not get(api, "skills/" + SHIPPED)["skill"]["from_tico"]
    used = row(get(api, "skills/usage?days=30", "ben-test")["skills"], SHIPPED)
    assert used["uses"] == 0 and used["bots"] == 0
    detail = get(api, f"skills/{SHIPPED}/usage?days=30", "ben-test")
    assert detail["totals"]["uses"] == 0 and detail["actors"] == []


def test_a_persons_version_or_revival_makes_a_library_skill_the_companys(api):
    store = api.app.state.store
    SL.forget()
    with store.transaction() as c:
        SL.seed_company(c, store.settings)
        c.execute("UPDATE skills SET deleted_at=? WHERE name='review-a-contract'", (H.now(),))  # as if retired
    assert get(api, "skills/review-a-contract")["skill"]["id"] is None  # retired: only what its roles ship
    publish(api, name=SHIPPED)  # a person's version on top of Tico's
    publish(api, name="review-a-contract")  # a person brings a retired one back
    listed = get(api, "skills")["skills"]
    for name in (SHIPPED, "review-a-contract"):
        assert not row(listed, name)["from_tico"] and row(listed, name)["latest"]["number"] == 2
        detail = get(api, "skills/" + name)
        assert not detail["skill"]["from_tico"] and detail["skill"]["latest"]["approved_by_id"] == "ben"
