"""Attachment listing/downloads through the CLI, with no network or real mailbox."""

import base64, json, os, stat, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fake  # noqa: E402
from harness import Stage2  # noqa: E402

from connectors.mail import gmail as gm  # noqa: E402


PDF = b"%PDF-1.7\nMurphy evidence\n%%EOF\n"
INLINE = b"inline evidence\x00\xff"


def encoded(data):
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def attachment_message():
    remote = {
        "partId": "1.0",
        "mimeType": "application/pdf",
        "filename": "../../Murphy\nCase?.pdf",
        "headers": [],
        "body": {"attachmentId": "gmail-pdf-id", "size": len(PDF)},
    }
    inline = {
        "partId": "1.1",
        "mimeType": "application/octet-stream",
        "filename": "..\\inline?.bin",
        "headers": [],
        "body": {"data": encoded(INLINE), "size": len(INLINE)},
    }
    unnamed = {
        "partId": "1.2",
        "mimeType": "application/octet-stream",
        "filename": "",
        "headers": [{"name": "Content-Disposition", "value": "attachment"}],
        "body": {"data": encoded(b"unnamed"), "size": 7},
    }
    nested = {
        "partId": "1",
        "mimeType": "multipart/mixed",
        "filename": "",
        "body": {},
        "headers": [],
        "parts": [remote, inline, unnamed],
    }
    return fake.message(
        "case-message", headers={"Subject": "Murphy matter"}, parts=[fake.part("text/plain", "See attached."), nested]
    )


class Attachments(Stage2):
    manifests = {
        "influencer": """
name: influencer
outbound_send: false
access:
  - service: gmail
    identity: "ana@acme.example"
    can: [read]
""",
        "legal": """
name: legal
outbound_send: false
access:
  - service: gmail
    identity: "ana@acme.example"
    can: [read]
    read_only: true
""",
        "outsider": """
name: outsider
outbound_send: false
access:
  - service: slack
    identity: "Acme workspace"
    can: [read]
""",
    }

    def corpus(self):
        return [attachment_message()]

    def setUp(self):
        super().setUp()
        # macOS exposes /var as a symlink; use the canonical temp root for no-symlink tests.
        self.output_root = self.root.resolve()
        self.service.attachment_data[("case-message", "gmail-pdf-id")] = {"data": encoded(PDF), "size": len(PDF)}

    def test_existing_destination_traversal_and_symlink_are_never_overwritten(self):
        existing = self.output_root / "existing.pdf"
        existing.write_bytes(b"keep")
        symlink = self.output_root / "link.pdf"
        symlink.symlink_to(existing)
        cases = (
            (existing, "overwrite"),
            (symlink, "symlink"),
            (self.output_root / "child" / ".." / "escape.pdf", "traversal"),
        )
        for target, expected in cases:
            with self.subTest(target=target):
                rc, _, err = self.run_cli(
                    "attachment",
                    "case-message",
                    "a1",
                    "--out",
                    str(target),
                    "--as",
                    "legal",
                    "--mailbox",
                    "ana@acme.example",
                )
                self.assertEqual(rc, 2)
                self.assertIn(expected, err.lower())
        self.assertEqual(existing.read_bytes(), b"keep")
        self.assertEqual(self.service.calls, [])


if __name__ == "__main__":
    import unittest

    unittest.main()
