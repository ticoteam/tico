"""A send approval's files in Needs you link only to a stored version whose sha256 and size are the approved ones."""
import hashlib

from backend.tests.test_api import api, headers, post, setup_attempt  # noqa: F401


def meta(name, data, file_id):
    return {"name": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(), "file_id": file_id}


def test_needs_you_links_only_the_approved_bytes_and_keeps_that_version(api):
    tid = post(api, "tasks", {"owner": "ops", "title": "Send the report", "body": "Mail it to Ava."})["id"]
    report = post(api, f"tasks/{tid}/files", {"name": "report.md", "text": "# Report"})
    other = post(api, f"tasks/{tid}/files", {"name": "notes.md", "text": "# Notes"})
    payload = {"to": ["ava@creator.example"], "cc": [], "subject": "Report", "body_sha256": "a" * 64,
               "mailbox": "ana@acme.example", "draft": "r-1",
               "attachments": [meta("report.md", b"# Report", report["file_id"]),
                               meta("notes.md", b"# Not these notes", other["file_id"]),   # the bytes differ
                               meta("gone.md", b"# Gone", "no-such-file-0001"),
                               {"name": "plain.md", "size": 3, "sha256": "c" * 64}]}
    approval = post(api, "approvals", {"kind": "send", "payload": payload, "task_id": tid},
                    token=setup_attempt(api)[2]["token"])

    def links():
        items = api.get("/api/v2/needs-you", headers=headers()).json()["items"]
        return next(i for i in items if i["id"] == approval["id"])["attachment_files"]

    assert links() == [{"file_id": report["file_id"], "version": 1}, None, None, None]
    # A new version of the linked file does not move the link: v1 still holds the approved bytes.
    assert post(api, f"tasks/{tid}/files", {"name": "report.md", "text": "# Report, changed"})["version"] == 2
    assert links()[0] == {"file_id": report["file_id"], "version": 1}
    assert api.get(f"/api/v2/files/{report['file_id']}?v=1", headers=headers()).content == b"# Report"
    assert api.get(f"/api/v2/files/{report['file_id']}", headers=headers()).content == b"# Report, changed"
