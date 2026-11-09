"""Who sees a members-only pipeline, and who edits its rules (Track N, shared predicate). Its owner, its manager and its
members see it, and so do its editors; anyone else does not. Only a person edits rules, and a bot owner row edits nothing.
The owner, manager and members may be stored by slug; a bot is read by its id, so both forms name the same bot. An Admin
edits a members-only pipeline's rules without seeing it: seeing is for the owner, the manager and the members."""

from backend import access
from backend import hubdb as H
from backend import pipeline_rights as P
from backend.tests.helpers.pipeline_foundation import ANA, T, boot, hub, insert

STEPS = [{"name": "Doing", "status": "doing"}, {"name": "Done", "status": "done"}]
OUTSIDER = "human:dana"  # on no roster and no owner or Admin list: sees only what is company-wide


def admins_are_chris(monkeypatch):
    monkeypatch.setattr(access, "owner_and_admin_emails", lambda conn: ["chris@acme.example"])


def test_a_members_only_pipeline_is_seen_by_its_owner_manager_and_members_only(tmp_path, monkeypatch):
    admins_are_chris(monkeypatch)
    path = tmp_path / "hub.sqlite"
    boot(path)
    c = hub(path)
    legal = H.type_create(c, ANA, "Legal", STEPS, mover=True)["id"]
    sales = H.type_create(c, ANA, "Sales", STEPS, mover=True)["id"]
    c.execute(
        "UPDATE task_types SET pipeline_owner_id=?, pipeline_visibility='members', manager=? WHERE id=?",
        (ANA, "legal-manager", legal),
    )
    # The manager is stored as its slug (the way setup writes it) and read as the bot's id.
    insert(c, "bots", id="b-42", slug="legal-manager", display_name="Legal Manager", state="active", created_at=T)
    insert(c, "bots", id="b-43", slug="legal-reviewer", display_name="Legal Reviewer", state="active", created_at=T)

    assert P.may_see(c, ANA, legal)  # the owner
    assert P.may_edit_rules(c, "human:chris", legal) and not P.may_see(
        c, "human:chris", legal
    )  # an Admin edits, no more
    assert P.may_see(c, "bot:b-42", legal)  # the manager, by slug in the row
    assert not P.may_see(c, OUTSIDER, legal)
    assert P.visible_pipeline_ids(c, OUTSIDER, [legal, sales, "no-such-pipeline"]) == {sales}

    # Members are stored as a person, and as a bot by slug; both see it, and nobody else does.
    assert not P.may_see(c, "human:cara", legal)
    insert(c, "pipeline_members", pipeline_id=legal, actor_id="human:cara", created_at=T)
    insert(c, "pipeline_members", pipeline_id=legal, actor_id="bot:legal-reviewer", created_at=T)
    assert P.may_see(c, "human:cara", legal)
    assert P.may_see(c, "bot:b-43", legal)
    assert not P.may_see(c, "bot:b-44", legal)


def test_only_a_person_edits_rules_and_a_bot_owner_row_edits_nothing(tmp_path, monkeypatch):
    admins_are_chris(monkeypatch)
    path = tmp_path / "hub.sqlite"
    boot(path)
    c = hub(path)
    legal = H.type_create(c, ANA, "Legal", STEPS, mover=True)["id"]
    c.execute("UPDATE task_types SET pipeline_owner_id=? WHERE id=?", (ANA, legal))
    insert(c, "bots", id="b-42", slug="legal-manager", display_name="Legal Manager", state="active", created_at=T)

    assert P.may_edit_rules(c, ANA, legal)
    assert not P.may_edit_rules(c, "bot:b-42", legal)  # the manager is never an editor
    assert not P.may_edit_rules(c, OUTSIDER, legal)

    # A row whose owner is a bot (the column accepts any text) does not make that bot an editor.
    c.execute("UPDATE task_types SET pipeline_owner_id=? WHERE id=?", ("bot:b-42", legal))
    assert not P.may_edit_rules(c, "bot:b-42", legal)
    assert "bot:b-42" not in P.editable_pipeline_ids(c, "bot:b-42", [legal])


def test_the_company_owner_sees_a_members_only_pipeline_and_an_admin_does_not(tmp_path, monkeypatch):
    admins_are_chris(monkeypatch)
    monkeypatch.setattr(access, "owner_email", lambda conn: "ana@acme.example")  # the owner's address, not an Admin's
    path = tmp_path / "hub.sqlite"
    boot(path)
    c = hub(path)
    legal = H.type_create(c, ANA, "Legal", STEPS, mover=True)["id"]
    c.execute(
        "UPDATE task_types SET pipeline_owner_id=?, pipeline_visibility='members' WHERE id=?", ("human:ben", legal)
    )

    assert P.may_see(c, "human:ben", legal)  # the pipeline's own owner
    assert P.may_see(c, ANA, legal)  # the company owner, who owns neither the pipeline nor a seat on its roster
    assert not P.may_see(c, "human:chris", legal)  # an Admin, who edits rules but does not see the pipeline
    assert P.may_edit_rules(c, "human:chris", legal)
    assert P.visible_pipeline_ids(c, "human:chris", [legal]) == set()
    insert(c, "pipeline_members", pipeline_id=legal, actor_id="human:cara", created_at=T)
    assert P.may_see(c, "human:cara", legal)  # a member
