"""Signing a PDF in Tico (backend/file_signings.py): a person's saved signature, and the signed bytes saved as the next
version of the same task file with a record of who signed which version, when, and the SHA-256 of each."""

import base64
import hashlib
import json

from backend.auth import Identity
from backend.store import H
from backend.tests.helpers import engine as X
from backend.tests.helpers.api import get, headers, unrelated_task_managers

ORIGINAL = b"%PDF-1.7\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
SIGNED = b"%PDF-1.7\n1 0 obj<<>>endobj\n2 0 obj<</Signed true>>endobj\ntrailer<<>>\n%%EOF\n"
# A 1x1 PNG, as the browser's signature pad sends it.
PNG = (
    "data:image/png;base64,"
    + base64.b64encode(
        bytes.fromhex(
            "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
            "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082"
        )
    ).decode()
)
MARKS = json.dumps([{"kind": "signature", "page": 1}, {"kind": "date", "page": 1}])


def task_pdf(api, name="declaration.pdf", data=ORIGINAL):
    """A task Ana asked for, with a PDF on it: (task id, file id)."""
    with api.app.state.store.transaction() as c:
        tid = H.task_create(c, "human:ana", "Sign the declaration", "Sign and return it.", "bot:ops", lint=False)["id"]
    r = api.post(f"/api/v2/tasks/{tid}/files", files={"file": (name, data, "application/pdf")}, headers=headers())
    assert r.status_code == 200, r.text
    return tid, r.json()["file_id"]


def sign(api, fid, version=1, data=SIGNED, token="ana-test", key=None, marks=MARKS):
    return api.post(
        f"/api/v2/files/{fid}/signings",
        data={"version": str(version), "marks": marks},
        files={"file": ("declaration.pdf", data, "application/pdf")},
        headers={**headers(token, key), "User-Agent": "Mozilla/5.0 (Macintosh) Safari/605.1.15"},
    )


def as_bot(api, slug="ops"):
    token = slug + "-bot-test"
    with api.app.state.store.transaction() as c:
        X.bot(c, slug)
        c.execute(
            "INSERT OR IGNORE INTO agents(bot,harness,token_hash,created_at,created_by) VALUES(?,?,?,?,?)",
            (slug, "hermes", "x-" + slug, H.now(), "human:ana"),
        )
    api.app.state.auth.settings.test_identities[token] = Identity("bot:" + slug, "bot", agent="hermes")
    return token


def test_a_person_keeps_one_signature_of_their_own_and_may_replace_or_remove_it(api):
    assert get(api, "me/signature")["signature"] is None
    saved = api.put("/api/v2/me/signature", json={"image": PNG}, headers=headers())
    assert saved.status_code == 200, saved.text
    assert get(api, "me/signature")["signature"]["image"] == PNG
    assert get(api, "me/signature", token="ben-test")["signature"] is None  # each person's own
    not_png = api.put("/api/v2/me/signature", json={"image": "data:image/svg+xml;base64,PHN2Zz4="}, headers=headers())
    assert not_png.status_code == 422, not_png.text
    assert api.delete("/api/v2/me/signature", headers=headers()).status_code == 200
    assert get(api, "me/signature")["signature"] is None


def test_signing_adds_the_next_version_of_the_same_file_and_records_both_versions_hashes(api):
    tid, fid = task_pdf(api)
    key = "sign-once"
    r = sign(api, fid, key=key)
    assert r.status_code == 200, r.text
    done = r.json()
    assert (done["file_id"], done["version"]) == (fid, 2)
    # The version signed is kept as it was; the signed bytes are the new one.
    assert api.get(f"/api/v2/files/{fid}/versions/1", headers=headers()).content == ORIGINAL
    assert api.get(f"/api/v2/files/{fid}/versions/2", headers=headers()).content == SIGNED
    record = get(api, f"files/{fid}/signings")["signings"]
    assert len(record) == 1
    row = record[0]
    assert row["signer"] == "human:ana" and row["signer_name"]
    assert (row["before_version"], row["after_version"]) == (1, 2)
    assert row["before_sha256"] == hashlib.sha256(ORIGINAL).hexdigest()
    assert row["after_sha256"] == hashlib.sha256(SIGNED).hexdigest()
    assert row["marks"] == [{"kind": "signature", "page": 1}, {"kind": "date", "page": 1}]
    assert row["user_agent"].startswith("Mozilla/5.0") and "ip_address" in row  # the test client has no address
    # The task's file list and the file's versions say which version the signing made.
    listed = next(f for f in get(api, f"tasks/{tid}/files")["files"] if f["id"] == fid)
    signed = {v["n"]: v["signed"] for v in listed["versions"]}
    assert signed[1] is None and signed[2]["by"] == "human:ana" and signed[2]["at"] == row["created_at"]
    versions = {v["version"]: v["signed"] for v in get(api, f"files/{fid}/versions")["versions"]}
    assert versions[1] is None and versions[2]["by_name"] == row["signer_name"]
    # A retry of the same send lands once.
    again = sign(api, fid, key=key)
    assert again.status_code == 200 and again.json()["version"] == 2
    assert len(get(api, f"files/{fid}/signings")["signings"]) == 1
    # Marks drawn on an older version are refused, never saved over the newer one.
    stale = sign(api, fid, version=1)
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "stale_version"
    with api.app.state.store.read() as c:
        assert c.execute("SELECT current_version FROM bot_files WHERE id=?", (fid,)).fetchone()[0] == 2


def test_only_a_person_in_their_own_session_signs_and_a_bot_reads_the_record_without_their_address(api):
    from backend.tests.helpers.assistant import assistant_turn

    tid, fid = task_pdf(api)
    with api.app.state.store.transaction() as c:
        unrelated_task_managers(c, "ops")
        c.execute("UPDATE tasks SET private=1 WHERE id=?", (tid,))
    bot = as_bot(api)
    refused = sign(api, fid, token=bot)
    assert refused.status_code == 403, refused.text
    _, attempt = assistant_turn(api, "ana-test")
    assert sign(api, fid, token=attempt["token"]).status_code == 403
    assert api.put("/api/v2/me/signature", json={"image": PNG}, headers=headers(attempt["token"])).status_code == 403
    assert sign(api, fid, data=b"not a pdf").status_code == 422
    assert sign(api, fid, marks="[]").status_code == 422
    assert sign(api, fid, token="cara-test").status_code == 404  # a private task she is not on
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM file_signings").fetchone()[0] == 0
        assert c.execute("SELECT current_version FROM bot_files WHERE id=?", (fid,)).fetchone()[0] == 1
    assert sign(api, fid).status_code == 200
    seen = get(api, f"files/{fid}/signings", token=bot)["signings"]
    assert [s["after_version"] for s in seen] == [2] and seen[0]["signer"] == "human:ana"
    assert "ip_address" not in seen[0] and "user_agent" not in seen[0]
    assert get(api, f"files/{fid}/signings", token="cara-test", expected=404)


def test_only_a_pdf_on_a_task_is_signed(api):
    with api.app.state.store.transaction() as c:
        tid = H.task_create(c, "human:ana", "Notes", "Keep notes.", "bot:ops", lint=False)["id"]
    r = api.post(f"/api/v2/tasks/{tid}/files", files={"file": ("notes.txt", b"plain", "text/plain")}, headers=headers())
    fid = r.json()["file_id"]
    refused = sign(api, fid)
    assert refused.status_code == 422 and "PDF" in refused.json()["error"]["detail"]
