"""Keys kept out of task text (backend/task_text_keys.py, spec "Keep keys out of task comments and bot memory").

Typed in Tico, a task text write holding a certain-tier key is refused before any route runs: 422 looks_like_key,
nothing stored, the task at the same version, one audit row with no value. From Slack it is stored with the key as a
mark. Ordinary text (ids, hashes, links, prose about keys) is stored unchanged.
"""

import json
from pathlib import Path

from backend import task_text_keys as K
from backend import threads
from backend.store import H
from backend.tests.helpers.tasks_board import api, bot_token, get, headers, post  # noqa: F401

CASES = json.loads((Path(__file__).parent / "fixtures" / "secret_drop_cases.json").read_text())
# Stored split, joined here, so this file holds no key either.
KEY = "".join(["gh", "p_", "aB3dE5fG7hJ9kL1mN3pQ5rS7tU9vW1xY3z"])
# What Tico writes on tasks every day, and prose about keys (the spec page's own words, which a draft rule flagged).
ORDINARY = [
    "7bb45a16-a9b9-4b50-9eaa-9262117bc41d",
    "file-8d19d6b6a8e1db42533c3cd7",
    "0a2a3bc6b4f1e2d3c4b5a69788796a5b4c3d2e1f",
    "https://github.com/acme/tico/pull/4085",
    "s3://acme-bucket/runs/2026-11-17/log.txt",
    "Tico-Run: f9054cd0-49b2-4cff-9c26-f58f9df53045",
    "A key pasted into a task comment is stored and shown to every reader and bot: the key detector guards only the "
    "Tools chat. The turn scrub also misses keys written as JSON escapes or UTF-16.",
    "Check every task text field with the detector's rules; teach the scrub the encoded forms.",
    "Same comment -> 422, a key was found, save it in Tools. Nothing stored; an audit row, no value.",
    "Given a refusal to a bot, when it reads the answer, then it gets the stable code looks_like_key and one line it "
    "can act on: save it in Tools and refer to it by name.",
    "Given a memory file with an Authorization: line, when a turn changes only its value to a granted secret, then "
    "the new value is masked before the commit.",
    "token= and the value on the next line, in UTF-8 and UTF-16",
    "Save GITHUB_TOKEN in Tools and refer to it by name; the api key is in Tools as STRIPE_SECRET_KEY.",
    # A name, a colon and a word, a number or a dotted name: what engineering notes say every day (Review 1, run 5).
    "Motion (tokens.css: --dur-fast, --dur, --ease)",
    "auth: OAuth2 device flow",
    "hotkey: Ctrl-K opens search",
    "max_tokens: 4096",
    "cache key: tasks:list:v2",
    # Names with a version, a dashed hash or a Tico file id after a key word (QA on 30c1714, N1).
    "key: build2026",
    "cache key: sha1-30c1714c9",
    "api key: file-8d19d6b6a8e1db42533c3cd7",
    "token: gpt-4o-mini",
    "auth: sha256",
    # Words after a key word, camelCase names included.
    "token: unknown",
    "auth: authentication",
    "cache key: taskListByOwnerCache",
]
LETTERS_12 = "qzhvkrtw" + "mxbn"  # 12 synthetic random letters (Review 1 on 8968b9f)
# Synthetic keys a word in front of them names, which ordinary text never holds: letters only, short hex, mixed.
# Built from pieces, so this file holds no key-shaped literal.
NAMED_KEYS = [
    "api key: " + "qzhvkrtw" * 4 + "mxbn",  # 36 lowercase letters
    "token: " + "XKQWZPTRVB" * 2,  # 20 uppercase letters
    "token: " + LETTERS_12,
    "token=" + "a1b2" + "c3d4",
    "api_key=" + "Xk29fLq8" + "Zr7Tn3Wd",
    "secret: " + "dGhpcyBp" + "cyBhIGtleQ==",
    "token: " + "9f86d081-884c-4d63-a0d1-" + "7f0e1e1f2b3c",  # a UUID that names nothing here
]
# The spec page's own text (people's names changed to the fictional team's).
SPEC_PAGE = (Path(__file__).parent / "fixtures" / "task_text_spec_page.txt").read_text()


def _negatives():
    joined = ["".join(c["text"]) if isinstance(c["text"], list) else c["text"] for c in CASES["cases"]]
    certain_free = [
        t for t, c in zip(joined, CASES["cases"]) if not any(e.get("tier") == "certain" for e in c["expect"])
    ]
    return certain_free + list(CASES["ordinary"]) + ORDINARY + [SPEC_PAGE]


def _task(api, token="ana-test"):
    return post(api, "tasks", {"owner": "ops", "title": "Review the report", "body": "Please review."}, token=token)


def _rows(api, task_id):
    with api.app.state.store.read() as c:
        return [
            dict(r)
            for r in c.execute(
                "SELECT actor, target, detail FROM events WHERE action=? AND target=?", (K.ACTION, task_id)
            )
        ]


def _shown(api, task_id):
    return get(api, f"tasks/{task_id}")["task"]


def _refused(api, path, body, token="ana-test"):
    r = api.post("/api/v2/" + path, json=body, headers={"Authorization": "Bearer " + token})
    assert r.status_code == 422, r.text
    error = r.json()["error"]
    assert error["code"] == "looks_like_key" and "Tools" in error["detail"]
    assert KEY not in r.text and KEY[4:12] not in r.text  # the value, or part of it, is never repeated
    return error


def test_a_person_s_comment_holding_a_key_is_refused_and_nothing_is_stored(api):
    task = _task(api)
    before = _shown(api, task["id"])
    _refused(api, f"tasks/{task['id']}/comments", {"text": "deploy key: " + KEY})
    after = _shown(api, task["id"])
    assert after["version"] == before["version"]
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM messages WHERE body LIKE ?", ("%" + KEY + "%",)).fetchone()
    # One audit row: the task, the author, the rule. No value, hash or partial characters.
    [row] = _rows(api, task["id"])
    detail = json.loads(row["detail"])
    assert row["actor"] == "human:ana" and detail["outcome"] == "refused" and detail["rules"] == ["github"]
    assert KEY[4:] not in row["detail"] and KEY[-8:] not in row["detail"]


def test_a_bot_is_refused_the_same_way_with_the_stable_code(api):
    task = _task(api)
    token = bot_token(api, "ops")
    _refused(api, f"tasks/{task['id']}/comments", {"text": "the token is " + KEY}, token=token)
    [row] = _rows(api, task["id"])
    assert row["actor"] == "bot:ops"


def test_every_other_task_text_write_is_refused_and_the_task_stays_at_its_version(api):
    task = _task(api)
    said = post(api, f"tasks/{task['id']}/comments", {"text": "Ordinary words"})["comment"]
    version = _shown(api, task["id"])["version"]
    _refused(api, f"tasks/{task['id']}/comments/{said['id']}", {"text": "now " + KEY})  # a comment edit
    _refused(api, f"tasks/{task['id']}", {"title": "Use " + KEY})
    _refused(api, f"tasks/{task['id']}", {"note": "export GITHUB_TOKEN=" + KEY})
    _refused(api, f"tasks/{task['id']}", {"public_title": "x", "public_summary": KEY})
    _refused(api, f"tasks/{task['id']}/done", {"note": "Done; it used " + KEY})  # inside a hand-off
    _refused(api, "tasks", {"owner": "ops", "title": "New", "body": "key " + KEY})  # a create
    shown = _shown(api, task["id"])
    assert shown["version"] == version and shown["status"] == "open" and shown["title"] == "Review the report"
    assert len(_rows(api, task["id"])) == 5


def test_a_key_from_slack_on_a_task_is_stored_as_a_mark_with_an_audit_row(api):
    task = _task(api)
    with api.app.state.store.transaction() as c:
        thread = threads.thread_row(c, task["conversation_id"])
        out = K.mask_outside(c, thread, "human:ana", "the key " + KEY + " is for deploys", "slack")
        kept = K.mask_outside(c, thread, "human:ana", "the key " + KEY, "")  # typed in Tico: the guard's, not here
    assert out == "the key " + K.MARK + " is for deploys" and KEY in kept
    [row] = _rows(api, task["id"])
    assert json.loads(row["detail"])["outcome"] == "masked" and KEY[4:] not in row["detail"]
    assert "Slack" in K.told("slack")


def test_a_slack_post_into_a_task_s_conversation_is_stored_masked(api):
    task = _task(api)
    with api.app.state.store.transaction() as c:
        posted = threads.post(c, "human:ana", task["conversation_id"], "here: " + KEY, kind="say", via="slack")
    assert KEY not in posted["body"] and K.MARK in posted["body"]


def test_ordinary_text_and_every_detector_negative_is_stored_unchanged(api):
    negatives = _negatives()
    assert [t for t in negatives if K.found(t)] == []
    task = _task(api)
    text = "\n".join(ORDINARY)
    said = post(api, f"tasks/{task['id']}/comments", {"text": text})["comment"]
    assert said["body"] == text and _rows(api, task["id"]) == []


def test_every_detector_key_is_still_refused_in_task_text():
    # A name in front of a value is not enough on its own in task text; the detector's own keys all still count.
    for case in CASES["cases"]:
        text = "".join(case["text"]) if isinstance(case["text"], list) else case["text"]
        certain = [e for e in case["expect"] if e.get("tier") == "certain"]
        assert len(K.found(text)) >= len(certain), case.get("id")


def test_a_named_key_is_refused_however_it_is_spelled(api):
    task = _task(api)
    for line in NAMED_KEYS:
        assert K.found(line), line
        _refused(api, f"tasks/{task['id']}/comments", {"text": line})
    assert len(_rows(api, task["id"])) == len(NAMED_KEYS)


def test_a_short_letters_only_key_from_slack_is_stored_as_a_mark(api):
    task = _task(api)
    with api.app.state.store.transaction() as c:
        posted = threads.post(c, "human:ana", task["conversation_id"], "token: " + LETTERS_12, kind="say", via="slack")
    assert LETTERS_12 not in posted["body"] and K.MARK in posted["body"]


def test_this_team_s_own_ids_after_a_key_word_are_stored_unchanged(api):
    task = _task(api)
    text = f"token: {task['id']}\napi key: {task['conversation_id']}\napi key: file-8d19d6b6a8e1db42533c3cd7"
    said = post(api, f"tasks/{task['id']}/comments", {"text": text})["comment"]
    assert said["body"] == text and _rows(api, task["id"]) == []


def test_a_file_holding_a_key_still_uploads_with_ordinary_text(api):
    # Only the form's text fields are task text: the file's bytes are not read as one blob with them.
    task = _task(api)
    r = api.post(
        f"/api/v2/uploads/threads/{task['conversation_id']}/messages",
        data={"text": "the log"},
        files={"files": ("run.log", ("export GITHUB_TOKEN=" + KEY).encode(), "text/plain")},
        headers=headers(),
    )
    assert r.status_code == 200, r.text


def _upload_refused(api, path, data):
    files = {"files": ("a.txt", b"hello", "text/plain")}
    r = api.post("/api/v2/" + path, data=data, files=files, headers=headers())
    assert r.status_code == 422 and r.json()["error"]["code"] == "looks_like_key", r.text
    assert KEY not in r.text


def test_a_comment_or_a_new_task_sent_with_a_file_is_refused_too(api):
    task = _task(api)
    _upload_refused(api, f"uploads/threads/{task['conversation_id']}/messages", {"text": "deploy key: " + KEY})
    _upload_refused(api, "uploads/tasks", {"owner": "ops", "title": "New", "body": "key " + KEY})
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM messages WHERE body LIKE ?", ("%" + KEY + "%",)).fetchone()
        assert not c.execute("SELECT 1 FROM tasks WHERE body LIKE ?", ("%" + KEY + "%",)).fetchone()
    assert len(_rows(api, task["id"])) == 1
    # The same upload with ordinary words goes through, its file included.
    r = api.post(
        f"/api/v2/uploads/threads/{task['conversation_id']}/messages",
        data={"text": "max_tokens: 4096"},
        files={"files": ("a.txt", b"hello", "text/plain")},
        headers=headers(),
    )
    assert r.status_code == 200, r.text


def test_a_message_or_an_edit_in_a_task_s_conversation_is_refused(api):
    task = _task(api)
    said = post(api, f"threads/{task['conversation_id']}/messages", {"text": "Ordinary words"})
    _refused(api, f"threads/{task['conversation_id']}/messages", {"text": "the key " + KEY})
    mid = (said.get("message") or said)["id"]
    r = api.patch(f"/api/v2/messages/{mid}", json={"text": "now " + KEY}, headers=headers())
    assert r.status_code == 422 and r.json()["error"]["code"] == "looks_like_key", r.text
    assert len(_rows(api, task["id"])) == 2


def test_files_pages_and_reads_are_not_checked():
    assert K.scope("POST", "/api/v2/tasks/t1/files") is None
    assert K.scope("POST", "/api/v2/tasks/t1/pages") is None
    assert K.scope("GET", "/api/v2/tasks/t1") is None
    assert K.scope("POST", "/api/v2/tasks/t1/asks/r1/answer") == "task"
    assert K.scope("POST", "/api/v2/questions") == "task"


def test_a_message_outside_a_task_is_left_to_the_tools_chat_s_own_backstop(api):
    dm = post(api, "threads/dm", {"members": ["bot:ops"]})
    post(api, f"threads/{dm['id']}/messages", {"text": "for github " + KEY})
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM events WHERE action=?", (K.ACTION,)).fetchone()
    assert H.now()
