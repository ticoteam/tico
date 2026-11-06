"""Company skills at run time: a runner reads only its attempt's bot's installs, bundles are checked by sha, receipts
upsert, and usage is one row per (session, tool id) for directory skills only, with a person's `/name` theirs."""
import hashlib
import json

from backend import skill_runtime as SR
from backend import skills
from backend.store import H
from backend.tests.test_api import api, get, post, runner, setup_attempt  # noqa: F401

BRIEF = [{"path": "SKILL.md", "content": "---\nname: acme-brief\ndescription: Acme brief\n---\nWrite the brief.\n"}]
BRIEF_2 = [{"path": "SKILL.md", "content": "---\nname: acme-brief\ndescription: Acme brief\n---\nShorter.\n"},
           {"path": "notes/style.md", "content": "Plain words. “Quotes” stay raw.\n"}]


def add_skill(c, name, versions, deleted=False):
    """A directory skill with approved versions (1..n), its latest the last. Returns (skill id, [version ids])."""
    now, sid, vids = H.now(), "sk_" + name, []
    c.execute("INSERT INTO skills(id,name,description,created_by_type,created_by_id,deleted_at,created_at,updated_at) "
              "VALUES(?,?,?,'Human','ana',?,?,?)", (sid, name, "", now if deleted else None, now, now))
    for n, files in enumerate(versions, 1):
        vid = f"skv_{name}_{n}"
        c.execute("INSERT INTO skill_versions(id,skill_id,number,sha256,files,description,proposal_id,approved_by_id,"
                  "approved_at,created_at) VALUES(?,?,?,?,?,?,?,'ana',?,?)",
                  (vid, sid, n, SR.bundle_sha(files), json.dumps(files), f"v{n}", "skp_x", now, now))
        vids.append(vid)
    if vids:
        c.execute("UPDATE skills SET latest_version_id=? WHERE id=?", (vids[-1], sid))
    return sid, vids


def install(c, sid, bot, pinned=None):
    now = H.now()
    c.execute("INSERT INTO skill_installs(id,skill_id,target_type,target_id,version_policy,pinned_version_id,"
              "installed_by_type,installed_by_id,created_at,updated_at) VALUES(?,?,'Bot',?,?,?,'Human','ana',?,?)",
              ("ski_" + sid + bot, sid, bot, "pinned" if pinned else "latest", pinned, now, now))


def tool(name, item_id, status="started", tool_name="Skill"):
    """A tool event as runner-rs/src/hosts/claude.rs sends it."""
    payload = {"kind": "tool", "thread_id": "th", "turn_id": "tu", "tool": tool_name, "status": status,
               "item_id": item_id, "summary": "", "parent_id": None}
    if name is not None:
        payload["skill"] = name
    return {"kind": "tool", "payload": payload}


def send_events(api, machine, aid, events, first_seq):
    batch = [{"seq": first_seq + i, **e} for i, e in enumerate(events)]
    post(api, f"attempts/{aid}/events", {"events": batch}, machine["token"])


def test_resolved_installs_follow_the_policy_and_skip_what_cannot_run(api):
    with api.app.state.store.transaction() as c:
        brief, (v1, _) = add_skill(c, "acme-brief", [BRIEF, BRIEF_2])
        latest, _ = add_skill(c, "acme-latest", [BRIEF])
        gone, _ = add_skill(c, "acme-gone", [BRIEF], deleted=True)
        empty, _ = add_skill(c, "acme-empty", [])
        other, (foreign,) = add_skill(c, "acme-other", [BRIEF_2])
        wrong, _ = add_skill(c, "acme-wrong-pin", [BRIEF])
        install(c, brief, "ops", pinned=v1)
        for sid in (latest, gone, empty):
            install(c, sid, "ops")
        install(c, wrong, "ops", pinned=foreign)        # a pin to another skill's version stages nothing
        install(c, other, "cpo")
        got = SR.resolved_installs(c, "Bot", "ops")
    assert got == [{"name": "acme-brief", "version": 1, "sha256": SR.bundle_sha(BRIEF), "description": "v1"},
                   {"name": "acme-latest", "version": 1, "sha256": SR.bundle_sha(BRIEF), "description": "v1"}]


def test_a_runner_reads_only_its_attempts_bot_installs(api):
    machine, _, attempt = setup_attempt(api, "ops")
    other_machine, _, other = setup_attempt(api, "cpo")
    with api.app.state.store.transaction() as c:
        mine, _ = add_skill(c, "acme-brief", [BRIEF])
        theirs, _ = add_skill(c, "acme-cpo", [BRIEF_2])
        install(c, mine, "ops")
        install(c, theirs, "cpo")
    aid = attempt["id"]
    want = {"skills": [{"name": "acme-brief", "version": 1, "sha256": SR.bundle_sha(BRIEF), "description": "v1"}]}
    assert get(api, f"attempts/{aid}/skills", machine["token"]) == want
    assert get(api, f"attempts/{aid}/skills", attempt["token"]) == want          # the run's own token
    assert get(api, f"attempts/{aid}/skills", other_machine["token"], expected=403)["error"]["code"] == "forbidden"
    assert get(api, f"attempts/{aid}/skills", other["token"], expected=403)       # another bot's run
    assert get(api, f"attempts/{aid}/skills", "ben-test", expected=403)
    assert get(api, f"attempts/{other['id']}/skills", other["token"])["skills"][0]["name"] == "acme-cpo"
    post(api, f"attempts/{aid}/skills/receipt", {"staged": []}, other_machine["token"], expected=403)


def test_bundles_are_served_by_sha_to_people_bots_and_runners(api):
    machine, _, attempt = setup_attempt(api, "ops")
    with api.app.state.store.transaction() as c:
        add_skill(c, "acme-brief", [BRIEF, BRIEF_2])
        add_skill(c, "acme-gone", [[{"path": "SKILL.md", "content": "gone"}]], deleted=True)
    sha = SR.bundle_sha(BRIEF_2)
    for token in ("ben-test", attempt["token"], machine["token"]):
        got = get(api, f"skills/bundles/{sha}", token)
        assert got == {"name": "acme-brief", "version": 2, "sha256": sha, "files": BRIEF_2}
    assert get(api, "skills/bundles/" + SR.bundle_sha([{"path": "SKILL.md", "content": "gone"}]), expected=404)
    assert get(api, "skills/bundles/" + "0" * 64, expected=404)
    assert get(api, "skills/bundles/not-a-sha", expected=404)
    with api.app.state.store.transaction() as c:      # files that no longer match their sha are never served
        c.execute("UPDATE skill_versions SET files=? WHERE sha256=?", (json.dumps(BRIEF), sha))
    assert get(api, f"skills/bundles/{sha}", expected=500)["error"]["code"] == "bundle_mismatch"


def test_the_bundle_sha_is_the_contracts_canonical_json():
    files = [{"path": "b.md", "content": "é\n\"q\"\t\x01"}, {"path": "SKILL.md", "content": "x"}]
    text = '[{"content":"x","path":"SKILL.md"},{"content":"é\\n\\"q\\"\\t\\u0001","path":"b.md"}]'
    assert SR.bundle_sha(files) == hashlib.sha256(text.encode()).hexdigest()
    # The same vector is checked in runner-rs/src/company_skills.rs.
    assert SR.bundle_sha(files) == SR.bundle_sha(list(reversed(files)))
    assert SR.bundle_sha is skills.bundle_sha                # one algorithm: clients/skills.py, as R's routes use


def test_a_receipt_upserts_one_row_per_skill_and_ignores_unknown_versions(api):
    machine, _, attempt = setup_attempt(api, "ops")
    with api.app.state.store.transaction() as c:
        sid, _ = add_skill(c, "acme-brief", [BRIEF, BRIEF_2])
    aid = attempt["id"]
    staged = [{"name": "acme-brief", "sha256": SR.bundle_sha(BRIEF)}, {"name": "acme-none", "sha256": "1" * 64},
              {"name": "acme-brief", "sha256": "2" * 64}]
    assert post(api, f"attempts/{aid}/skills/receipt", {"staged": staged}, machine["token"]) == {"kept": 1}
    post(api, f"attempts/{aid}/skills/receipt", {"staged": [{"name": "acme-brief", "sha256": SR.bundle_sha(BRIEF_2)}]},
         machine["token"])
    post(api, f"attempts/{aid}/skills/receipt", {"staged": [{"name": "../x", "sha256": "1" * 64}]}, machine["token"],
         expected=422)
    with api.app.state.store.read() as c:
        rows = c.execute("SELECT target_type,target_id,skill_id,sha256,source FROM skill_receipts").fetchall()
    assert [tuple(r) for r in rows] == [("Bot", "ops", sid, SR.bundle_sha(BRIEF_2), "runner")]


def test_skill_calls_in_events_count_once_per_tool_id_for_directory_skills_only(api):
    machine, _, attempt = setup_attempt(api, "ops")
    aid = attempt["id"]
    with api.app.state.store.transaction() as c:
        sid, _ = add_skill(c, "acme-brief", [BRIEF])
    post(api, f"attempts/{aid}/skills/receipt", {"staged": [{"name": "acme-brief", "sha256": SR.bundle_sha(BRIEF)}]},
         machine["token"])
    post(api, f"attempts/{aid}/started", {"thread_id": "fake-thread"}, machine["token"])
    send_events(api, machine, aid, [tool("acme-brief", "toolu_1"), tool("acme-brief", "toolu_1"),
                                    tool("repo-own", "toolu_2"), tool("acme-brief", "toolu_3", status="completed"),
                                    tool("acme-brief", "toolu_4", tool_name="Bash"), tool(None, "toolu_5"),
                                    tool("Acme Brief", "toolu_6")], 1)
    send_events(api, machine, aid, [tool("acme-brief", "toolu_1"), tool("acme-brief", "toolu_7")], 8)
    with api.app.state.store.read() as c:
        rows = c.execute("SELECT skill_id,sha256,actor_type,actor_id,requested_by_id,trigger,source,session_key,"
                         "tool_use_id FROM skill_usages ORDER BY tool_use_id").fetchall()
    assert [tuple(r) for r in rows] == [
        (sid, SR.bundle_sha(BRIEF), "Bot", "ops", None, "model", "runner", aid, "toolu_1"),
        (sid, SR.bundle_sha(BRIEF), "Bot", "ops", None, "model", "runner", aid, "toolu_7")]


def test_a_persons_slash_name_to_a_bot_is_their_use_of_an_installed_skill(api):
    with api.app.state.store.transaction() as c:
        sid, _ = add_skill(c, "acme-brief", [BRIEF])
        other, _ = add_skill(c, "acme-other", [BRIEF_2])
        install(c, sid, "ops")
        install(c, other, "cpo")              # installed, but not for the bot the message goes to
    asked = post(api, "chat/ops", {"text": "/acme-brief for the Q3 review"})
    post(api, "chat/ops", {"text": "/acme-other please"})
    post(api, "chat/ops", {"text": "/acme-brief-x please"})
    post(api, "chat/ops", {"text": "please run /acme-brief"})
    with api.app.state.store.read() as c:
        rows = c.execute("SELECT skill_id,actor_type,actor_id,requested_by_id,trigger,source,session_key,tool_use_id,"
                         "thread_id FROM skill_usages").fetchall()
    assert [tuple(r) for r in rows] == [(sid, "Bot", "ops", "ana", "person", "thread", asked["conversation_id"],
                                         asked["id"], asked["conversation_id"])]


def test_typed_name_reads_only_a_leading_slash_name():
    assert SR.typed_name("  /acme-brief now") == "acme-brief"
    assert SR.typed_name("/acme-brief") == "acme-brief"
    assert SR.typed_name("/acme-brief\nmore") == "acme-brief"
    assert SR.typed_name("/Acme") is None and SR.typed_name("/acme/x") is None and SR.typed_name("x /acme") is None
