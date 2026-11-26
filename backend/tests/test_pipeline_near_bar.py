"""Task #3451: a page bar saved close to one of Tico's bars but not the same is a company's own bar from then on, asked
whole rather than as the bar's calibrated parts. The save goes through, and its reply says so and how to put Tico's
text back; a bar that is Tico's, or clearly the company's own, saves with no note. Nothing is changed for the company."""

import json

from backend import pipeline_judges as P
from backend import pipeline_settings as PS
from backend.tests.helpers.fresh_install import booted, environment, signed_in  # noqa: F401

EDITED_QA = PS.QA_RUBRIC.replace("A QA page a person approves", "A QA page that must meet this bar")


def _record(rubric: str) -> dict[str, object]:
    return {
        "name": "Orders",
        "prefix": "OR",
        "stages": [
            {
                "name": "Check",
                "owner": "assignee",
                "requirements": [{"kind": "page", "line": "QA report", "settings": {"rubric": rubric}}],
            }
        ],
    }


def test_a_bar_close_to_ticos_saves_with_a_note_that_names_it_and_how_to_restore_it(environment):
    assert P.check_of(EDITED_QA) is None and P.shared_words(EDITED_QA, PS.QA_RUBRIC) >= P.NEAR_SHARE
    api = environment()
    r = api.post("/api/v2/pipelines/orders", json=_record(EDITED_QA), headers=signed_in())
    assert r.status_code == 200, r.text
    body = r.json()
    req = body["pipeline"]["stages"][0]["requirements"][0]
    assert req["settings"]["rubric"] == EDITED_QA, "the company's text is saved as it is"
    (note,) = body["notes"]
    assert note.startswith("The QA report bar on Check is close to Tico's QA bar but not the same"), note
    assert "judged as one whole question, not as Tico's 8 calibrated parts" in note
    assert f"tico requirement update {req['id']} --rubric {json.dumps(PS.QA_RUBRIC)}" in note


def test_a_bar_that_is_ticos_or_clearly_the_companys_own_saves_with_no_note(environment):
    api = environment()
    own = "Shows the launch page live at its address, and who signed it off."
    for n, rubric in enumerate((PS.QA_RUBRIC, PS.REVIEW_RUBRIC, own)):
        record = {**_record(rubric), "name": f"Orders {n}", "prefix": "O" + "ABC"[n]}
        r = api.post(f"/api/v2/pipelines/orders-{n}", json=record, headers=signed_in())
        assert r.status_code == 200, r.text
        assert "notes" not in r.json(), (rubric[:40], r.json().get("notes"))


def test_close_is_four_in_five_words_shared_with_one_of_ticos_bars_as_it_ships_now():
    assert P.near_bar(PS.REVIEW_RUBRIC) is None and P.near_bar(PS.LEGACY_REVIEW_RUBRIC) is None, "Tico's own"
    near = P.near_bar(PS.REVIEW_RUBRIC + " Name the reviewer's maker.")
    assert near == ("two_reviews", PS.REVIEW_RUBRIC)
    assert P.near_bar("Our own: " + PS.SPEC_RUBRIC.split(" Only")[0])[0] == "spec", "Tico's bar with a line cut"
    own = "Names the launch page's address and shows it live there, with who signed it off and when."
    assert P.near_bar(own) is None, "a few of Tico's words in a bar of the company's own"
    assert P.shared_words("a b c d e", "a b c d f") == 0.8 and P.shared_words("", "") == 0.0
    assert P.shared_words("a b", "a b c d e") == 1.0 and P.shared_words("a b c d e", "a b") == 0.4, (
        "out of the first's words"
    )


def test_close_is_measured_on_the_companys_words_so_a_bar_cut_from_ticos_is_close_but_a_line_lifted_from_it_is_not():
    """Review of #5299: four in five of the company bar's own words, not the longer text's. A QA bar with its last two
    sentences cut is all Tico's words and is close; one sentence lifted from Tico's bar covers too little of it."""
    cut = " ".join(PS.QA_RUBRIC.split(". ")[:-2]) + "."
    assert P.check_of(cut) is None and P.near_bar(cut) == ("qa", PS.QA_RUBRIC)
    lifted = PS.QA_RUBRIC.split(". ")[0] + "."
    assert P.shared_words(lifted, PS.QA_RUBRIC) == 1.0 and P.near_bar(lifted) is None
