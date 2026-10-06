"""The judging primitive (`clients/judge.py`): the contract, the two transports, the sets.

No network. The direct transport is exercised through its `opener` seam with a fake HTTP
response; the hub transport through a fake client. The question sets in `questions/` are
loaded for real, because a set that does not load is a broken release.
"""
import io
import json
import unittest
import urllib.error
from pathlib import Path

from clients import judge as J

ROOT = Path(__file__).resolve().parents[2]

QUESTIONS = {
    "bucket": {"type": "choice", "instructions": "Where does it go?",
               "criteria": {"archive": "noise", "reply": "answer it"}},
    "urgency": {"type": "score", "instructions": "How soon?", "criteria": ["never", "today"]},
    "is_ask": {"type": "noul", "instructions": "Is someone asking for something?"},
}
ANSWERS = {
    "bucket": {"type": "choice", "choice": "reply", "confidence": 0.8, "probabilities": {"archive": 0.2, "reply": 0.8}},
    "urgency": {"type": "score", "score": 0.7, "confidence": 0.6, "probabilities": {"0": 0.3, "1": 0.7}},
    "is_ask": {"type": "noul", "noul": 0.9},
}


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(status, body=b"{}"):
    return urllib.error.HTTPError("https://api.typesafe.ai/v1/systemone", status, "err", {}, io.BytesIO(body))


class Contract(unittest.TestCase):
    def test_the_shape_is_refused_before_the_network(self):
        with self.assertRaises(J.JudgeError) as caught:
            J.validate({}, {})
        self.assertEqual(caught.exception.code, "invalid")
        bad = [
            {"q": {"type": "guess", "instructions": "x"}},
            {"q": {"type": "choice", "instructions": "x", "criteria": {"only": "one"}}},
            {"q": {"type": "noul", "instructions": "x", "extra": 1}},
        ]
        for questions in bad:
            with self.assertRaises(J.JudgeError, msg=questions):
                J.validate({"a": 1}, questions)
        with self.assertRaises(J.JudgeError):
            J.validate("x" * (J.MAX_STATE_CHARS + 1), QUESTIONS)
        with self.assertRaises(J.JudgeError):
            J.validate({}, QUESTIONS, label="l" * (J.MAX_LABEL + 1))
        J.validate({"subject": "hi"}, QUESTIONS, label="mail-triage@1")


class Sets(unittest.TestCase):
    def test_registry_overrides_are_validated_and_explicit_roots_stay_authoritative(self):
        import os, tempfile
        from unittest import mock
        with tempfile.TemporaryDirectory() as registry:
            directory = Path(registry) / "questions"
            directory.mkdir()
            for name in ("listening-item", "mail-triage"):
                original = J.load_set(name, root=J.QUESTIONS_DIR)
                custom = {k: v for k, v in original.items() if k != "label"}
                custom["version"] += 1
                path = directory / (name + ".json")
                path.write_text(json.dumps(custom))
                with mock.patch.dict(os.environ, {"TICO_REGISTRY_DIR": registry}):
                    self.assertEqual(J.load_set(name)["label"], f"{name}@{custom['version']}")
                    self.assertEqual(J.load_set(name, root=J.QUESTIONS_DIR)["label"], original["label"])
                self.assertEqual(J.load_set(name, registry_dir=registry)["questions"], custom["questions"])
                path.write_text('{"id":"wrong"}')
                with self.assertRaises(J.JudgeError) as bad:
                    J.load_set(name, registry_dir=registry)
                self.assertEqual(bad.exception.code, "invalid")
                path.unlink()
                self.assertEqual(J.load_set(name, registry_dir=registry)["label"], original["label"])
            base = {"id": "listening-item", "version": 1, "summary": "fixture",
                    "questions": {"lead": {"type": "noul", "instructions": "A lead"}}}
            for invalid in ([], {**base, "questions": []}, {**base, "state": 3}, {**base, "questions": {
                    "lead": {"type": "choice", "dynamic": True, "instructions": "A lead", "criteria": []}}}):
                (directory / "listening-item.json").write_text(json.dumps(invalid))
                with self.assertRaises(J.JudgeError):
                    J.load_set("listening-item", registry_dir=registry)

    def test_registry_symlinks_and_nonfiles_never_read_outside_or_fall_back(self):
        import os, tempfile
        from unittest import mock
        cases = ("file-link", "registry-link", "invalid-json")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                registry, outside = root / "registry", root / "outside"
                registry.mkdir()
                questions = registry / "questions"
                questions.mkdir()
                outside.mkdir()
                (outside / "questions").mkdir()
                secret = "outside-question-content-must-not-be-read"
                data = {"id": "listening-item", "version": 99, "summary": secret,
                        "questions": {"lead": {"type": "noul", "instructions": secret}}}
                victim = outside / "questions/listening-item.json"
                victim.write_text(json.dumps(data))
                path = questions / "listening-item.json"
                if case == "file-link":
                    path.symlink_to(victim)
                elif case == "internal-link":
                    (questions / "source.json").write_text(json.dumps(data))
                    path.symlink_to("source.json")
                elif case == "directory-link":
                    questions.rmdir()
                    questions.symlink_to(victim.parent, target_is_directory=True)
                elif case == "registry-link":
                    questions.rmdir()
                    registry.rmdir()
                    registry.symlink_to(outside, target_is_directory=True)
                elif case == "dangling-file":
                    path.symlink_to(outside / "missing.json")
                elif case == "dangling-directory":
                    questions.rmdir()
                    questions.symlink_to(outside / "missing", target_is_directory=True)
                elif case == "file-directory":
                    path.mkdir()
                elif case == "directory-file":
                    questions.rmdir()
                    questions.write_text(secret)
                elif case == "file-fifo":
                    os.mkfifo(path)
                elif case == "read-failure":
                    path.write_text(json.dumps(data))
                elif case == "invalid-json":
                    path.write_text('{"outside-question-content-must-not-be-read": [}')
                else:
                    path.write_text(json.dumps({"id": secret}))
                outside_ids = {(p.stat().st_dev, p.stat().st_ino) for p in (outside, victim.parent, victim)}
                real_open = os.open

                def contained_open(*args, **kwargs):
                    if case == "read-failure" and args[0] == path.name:
                        raise PermissionError("private read error must not be exposed")
                    descriptor = real_open(*args, **kwargs)
                    info = os.fstat(descriptor)
                    self.assertNotIn((info.st_dev, info.st_ino), outside_ids, "an outside descriptor must never open")
                    return descriptor

                with mock.patch.object(J.os, "open", side_effect=contained_open):
                    with self.assertRaises(J.JudgeError) as refused:
                        J.load_set("listening-item", registry_dir=registry)
                self.assertEqual(refused.exception.code, "invalid")
                self.assertEqual(refused.exception.detail, "Invalid registry question set. Use a regular, valid JSON file in registry/questions.")
                self.assertNotIn(secret, str(refused.exception))

    def test_registry_file_swap_to_an_outside_symlink_is_refused(self):
        import os, tempfile
        from unittest import mock
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            questions = root / "registry/questions"
            questions.mkdir(parents=True)
            path = questions / "listening-item.json"
            path.write_text((J.QUESTIONS_DIR / path.name).read_text())
            victim = root / "outside.json"
            victim.write_text("private-outside-fixture")
            real_open = os.open

            def swapped_open(part, flags, **kwargs):
                if part == path.name:
                    path.unlink()
                    path.symlink_to(victim)
                return real_open(part, flags, **kwargs)

            with mock.patch.object(J.os, "open", side_effect=swapped_open), mock.patch.object(J.os, "fdopen") as read:
                with self.assertRaises(J.JudgeError):
                    J.load_set("listening-item", registry_dir=questions.parent)
                read.assert_not_called()


if __name__ == "__main__":
    unittest.main()
