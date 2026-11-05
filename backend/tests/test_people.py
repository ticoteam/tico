#!/usr/bin/env python3
"""Unit tests for the people roster (backend/people.py).

The documents are inline, in the shape `registry/people.yaml` and `registry/employees.yaml` use.
"""
import unittest

from backend import people as P

DOC = {
    "default_user": "ana",
    "teams": {"marketing": {"root": "cmo"}, "product": {"root": "cpo"},
              "human-ops": {"root": "human-ops"}, "sales": {"root": "sales"}},
    "people": [
        {"id": "ana", "name": "Ana Rivera", "email": "Ana@Acme.example", "title": "Founder",
         "team": "leadership", "primary_for": ["marketing"], "bot": None},
        {"id": "ben", "name": "Ben", "email": "ben@acme.example", "title": "Product",
         "team": "product", "primary_for": ["product"], "bot": None},
        {"id": "lena", "name": "Lena", "email": "lena@acme.example", "title": "Success",
         "team": "success", "primary_for": ["human-ops"], "bot": "success"},
        {"id": "priya", "name": "Priya", "email": "priya@acme.example", "title": "Sales",
         "team": "sales", "primary_for": ["sales", "lead-research"], "bot": "sales"},
        {"id": "omar", "name": "Omar", "email": "omar@acme.example", "title": "Sales",
         "team": "sales", "primary_for": [], "bot": "sales"},
    ],
}
OWNER = {"id": "ana", "email": "ana@acme.example"}
EMPLOYEES = {
    "cmo": {"name": "cmo", "reports_to": None},
    "seo": {"name": "seo", "reports_to": "cmo"},
    "listening": {"name": "listening", "reports_to": "cmo"},
    "cpo": {"name": "cpo", "reports_to": None},
    "bug-triage": {"name": "bug-triage", "reports_to": "cpo"},
    "sales": {"name": "sales", "reports_to": None},
    "lead-research": {"name": "lead-research", "reports_to": "sales"},
    "human-ops": {"name": "human-ops", "reports_to": None},
    "success": {"name": "success", "reports_to": "human-ops"},
    "coach": {"name": "coach", "reports_to": None},
    "loop-a": {"name": "loop-a", "reports_to": "loop-b"},
    "loop-b": {"name": "loop-b", "reports_to": "loop-a"},
}


class Primary(unittest.TestCase):
    ROSTER = P.load(DOC)

    def test_only_an_assigned_person_may_chat(self):
        self.assertTrue(P.may_chat("ana@acme.example", "seo", self.ROSTER, EMPLOYEES))
        self.assertFalse(P.may_chat("ben@acme.example", "seo", self.ROSTER, EMPLOYEES))
        self.assertTrue(P.may_chat("ben@acme.example", "bug-triage", self.ROSTER, EMPLOYEES))
        self.assertFalse(P.may_chat("unknown@acme.example", "bug-triage", self.ROSTER, EMPLOYEES))

    def test_signed_in_count_matches_turn_level_rules_without_building_groups(self):
        self.assertEqual(P.signed_in_count(DOC), 5)
        mixed = {"people": [
            {"id": "ana", "email": "ana@acme.example"},
            {"id": "guest", "email": ""},
            {"id": "left", "email": "left@acme.example", "hidden": True},
            {"id": "off", "email": "off@acme.example", "sign_in": False},
            {"id": "gone", "email": "gone@acme.example", "directory_left": True},
            {"id": "ben", "email": "ben@acme.example"},
        ]}
        self.assertEqual(P.signed_in_count(mixed), 2)
        self.assertEqual(len(P.load(mixed)["people"]), 6)


if __name__ == "__main__":
    unittest.main(verbosity=2)
