"""The org chart formula (backend/company_file/chart_formula): the golden cases (tests/fixtures/chart_formula_golden.json),
the rules the golden cannot show, the rows read back through the company file reader, and the contract additions the
formula reads: a strategy's channels and a bet's name and segment, in draft.py and yamlfile.py."""

import copy
import json
import re
from collections import Counter
from pathlib import Path

import pytest
import yaml

from backend.company_file import chart_formula as F
from backend.company_file import draft as D
from backend.company_file import yamlfile

FIXTURE = Path(__file__).parent / "fixtures" / "chart_formula_golden.json"
GOLDEN = json.loads(FIXTURE.read_text())
CASES = {case["name"]: case for case in GOLDEN["cases"]}
HOTMIC = CASES["hotmic"]["inputs"]
CARDS = {role["slug"]: role["department"] for role in D.ROLES}
# The six cards step 1a makes. Until they are in roles.json the formula names them, and the company file reader refuses
# them, so the read-back stands in an existing card for each one. test_the_stand_ins_go_once_the_six_cards_are_in_roles_json
# fails once all six exist, and then STAND_IN is deleted.
NEW_TEMPLATES = {"cro", "app-store", "affiliates", "lead-generation", "initiative-owner", "pipeline"}
STAND_IN = {
    "cro": "paid-media",
    "app-store": "product-marketing",
    "affiliates": "paid-media",
    "lead-generation": "sales",
    "initiative-owner": "product-manager",
    "pipeline": "devops-engineer",
}


def titles_of(result):
    return [seat["title"] for seat in result["employees"]]


def by_title(result, title):
    return next(seat for seat in result["employees"] if seat["title"] == title)


def named_bet(number, name, segment=""):
    return {"id": "b%d" % number, "statement": "Work on %s" % name.lower(), "name": name, "segment": segment}


def readback(case):
    """The case's chart written as a company file and read back: its rows, and the draft the reader makes of them. A
    template with no card in roles.json yet is read as its stand-in."""
    rows = [
        dict(
            seat,
            template=seat["template"]
            if seat["template"] in CARDS
            else STAND_IN.get(seat["template"], seat["template"]),
        )
        for seat in F.build(case["inputs"], case.get("removed", ()))["employees"]
    ]
    doc = {
        "version": "0.3",
        "created": "2026-11-07T00:00:00.000Z",
        "company": {"name": "Company", "about": "", "signals": []},
        "people": [{"id": "chris", "name": "Chris", "role": "Owner", "owner": True}],
        "employees": rows,
        "goals": [],
        "rules": {"mode": "defaults", "never_without_a_person": [], "review": "one"},
        "tools": {"mode": "defaults", "connect": []},
    }
    return rows, yamlfile.from_yaml(yaml.safe_dump(doc, sort_keys=False))


# ---------------------------------------------------------------------------------------------- the golden cases
@pytest.mark.parametrize("name", sorted(CASES))
def test_each_golden_case_builds_its_expected_chart(name):
    case = CASES[name]
    assert F.build(case["inputs"], case.get("removed", ())) == case["expected"]


def test_the_golden_fixture_is_a_test_fixture_not_part_of_the_install_bundle():
    assert FIXTURE.exists()
    assert not (Path(F.__file__).parent / "chart_formula_golden.json").exists()


def test_hotmic_is_the_spec_chart_with_twenty_six_seats_and_nothing_uncovered():
    result = F.build(HOTMIC)
    counts = Counter(seat["team"] for seat in result["employees"])
    assert counts == {
        "Product": 3,
        "Marketing": 7,
        "Sales": 4,
        "Engineering": 7,
        "Initiatives": 1,
        "Corporate": 3,
        "Support": 1,
    }
    assert len(result["employees"]) == 26
    assert result["uncovered"] == []


def test_build_is_deterministic_and_leaves_its_inputs_alone():
    inputs = copy.deepcopy(HOTMIC)
    first = F.build(inputs)
    assert F.build(inputs) == first
    assert inputs == HOTMIC


def test_a_seat_row_has_the_company_file_employee_shape():
    for seat in F.build(HOTMIC)["employees"]:
        assert set(seat) == {"id", "template", "title", "department", "reports_to", "team", "reason", "start"}
        assert seat["start"] is True


# ---------------------------------------------------------------------------------------------- titles and ids
def test_titles_and_ids_are_unique_within_their_limits_and_are_slugs():
    for case in CASES.values():
        seats = F.build(case["inputs"], case.get("removed", ()))["employees"]
        assert len({D.fold(seat["title"]) for seat in seats}) == len(seats)
        assert len({seat["id"] for seat in seats}) == len(seats)
        assert all(len(seat["title"]) <= D.LIMITS["title"] for seat in seats)
        assert all(len(seat["id"]) <= D.LIMITS["id"] for seat in seats)
        assert all(re.fullmatch(r"[a-z0-9][a-z0-9-]*", seat["id"]) for seat in seats)


def test_a_repeated_title_gets_a_number_and_a_repeated_id_a_suffix():
    inputs = {"segments": ["Creators", "creators", "CREATORS"], "channels": [], "initiatives": [], "industry": []}
    seats = [seat for seat in F.build(inputs)["employees"] if seat["template"] == "product-manager"]
    assert [seat["title"] for seat in seats] == [
        "Creators Product Owner",
        "creators Product Owner 2",
        "CREATORS Product Owner 3",
    ]
    assert [seat["id"] for seat in seats] == [
        "creators-product-owner",
        "creators-product-owner-2",
        "creators-product-owner-3",
    ]


def test_titles_that_slug_alike_get_ids_that_differ():
    inputs = {"segments": ["Pro/Max", "Pro Max"], "channels": [], "initiatives": [], "industry": []}
    seats = [seat for seat in F.build(inputs)["employees"] if seat["template"] == "product-manager"]
    assert [seat["title"] for seat in seats] == ["Pro/Max Product Owner", "Pro Max Product Owner"]
    assert [seat["id"] for seat in seats] == ["pro-max-product-owner", "pro-max-product-owner-2"]


def test_a_long_title_is_cut_so_its_number_still_fits():
    long_title = "Word " * 30  # an industry Owner's title is not capped on the way in
    industry = [{"title": long_title, "template": "billing"}, {"title": long_title, "template": "billing"}]
    inputs = {"segments": [], "channels": [], "initiatives": [], "industry": industry}
    titles = [seat["title"] for seat in F.build(inputs)["employees"] if seat["team"] == "Operations"]
    assert len(titles) == 2 and all(len(title) <= D.LIMITS["title"] for title in titles)
    assert titles[0] != titles[1] and titles[1].endswith(" 2")


def test_fixed_seats_claim_their_titles_and_ids_before_initiatives_and_industry_owners():
    bets = [named_bet(1, "Support"), named_bet(2, "Finance"), named_bet(3, "SEO")]
    industry = [{"title": "Support Owner", "template": "billing"}]
    result = F.build({"segments": [], "channels": [], "initiatives": bets, "industry": industry})
    seat = {row["title"]: row for row in result["employees"]}
    assert (seat["Support Owner"]["id"], seat["Support Owner"]["team"]) == ("support-owner", "Support")
    assert seat["Finance Owner"]["id"] == "finance-owner"
    assert seat["SEO Owner"]["id"] == "seo-owner"
    assert (seat["Support Owner 2"]["id"], seat["Support Owner 2"]["team"]) == ("support-owner-2", "Initiatives")
    assert seat["Finance Owner 2"]["id"] == "finance-owner-2"
    assert seat["SEO Owner 2"]["id"] == "seo-owner-2"
    assert seat["Support Owner 3"]["team"] == "Operations"  # the industry Owner clashes last, so it is the third
    teams = [row["team"] for row in result["employees"]]
    assert teams.index("Initiatives") < teams.index("Corporate") < teams.index("Support") < teams.index("Operations")


def test_a_name_with_no_ascii_letter_or_digit_gets_a_fallback_id():
    ids = {seat["title"]: seat["id"] for seat in F.build(CASES["unicode_and_emoji_names"]["inputs"])["employees"]}
    assert ids["Straße Händler Product Owner"] == "stra-e-h-ndler-product-owner"
    assert ids["🚀" * 40 + " Product Owner"] == "segment-2-product-owner"
    assert ids["🚀" * 40 + " Marketing Owner"] == "segment-2-marketing-owner"
    assert ids["🚀🚀 Owner"] == "initiative-b1-owner"


def test_lengths_are_counted_in_code_points_as_a_js_port_counts_them():
    assert len("🚀" * 41) == 41
    assert F.cut("🚀" * 41) == "🚀" * 40
    assert F.cut("Straße Händler") == "Straße Händler"


# ---------------------------------------------------------------------------------------------- initiatives
def test_a_bet_tagged_to_a_picked_segment_belongs_to_its_product_owner_and_an_untagged_one_gets_a_seat():
    result = F.build(HOTMIC)
    assert "Live rooms Owner" not in titles_of(result)
    assert "Photos are the input Owner" in titles_of(result)
    assert "“Live rooms” initiative (b1)" in by_title(result, "Creators Product Owner")["reason"]


def test_a_bet_tagged_to_a_segment_that_is_not_picked_is_untagged():
    inputs = {
        "segments": ["Creators"],
        "channels": [],
        "initiatives": [{"id": "b1", "statement": "Teach the teachers.", "name": "", "segment": "Teachers"}],
        "industry": [],
    }
    assert "Teach the teachers Owner" in titles_of(F.build(inputs))


def test_a_bet_is_tagged_to_a_segment_ignoring_case_and_spacing():
    inputs = {
        "segments": ["Creators "],
        "channels": [],
        "initiatives": [{"id": "b1", "statement": "Run live rooms", "name": "Live rooms", "segment": " creators"}],
        "industry": [],
    }
    result = F.build(inputs)
    assert "Live rooms Owner" not in titles_of(result)
    assert "“Live rooms” initiative (b1)" in by_title(result, "Creators Product Owner")["reason"]


def test_an_initiative_owner_reason_names_its_bet_and_its_statement():
    seat = by_title(F.build(HOTMIC), "Photos are the input Owner")
    assert seat["reason"] == "Owns the “Photos are the input” initiative (b3): “Photos are the input”."


def test_a_long_bet_statement_keeps_its_owner_reason_within_the_reason_limit():
    statement = "word " * 60
    inputs = {
        "segments": [],
        "channels": [],
        "initiatives": [{"id": "b5", "statement": statement, "name": "", "segment": ""}],
        "industry": [],
    }
    seat = next(row for row in F.build(inputs)["employees"] if row["team"] == "Initiatives")
    assert seat["reason"].startswith("Owns the “") and "(b5)" in seat["reason"]
    assert len(seat["reason"]) <= D.LIMITS["reason"]


def test_a_label_is_cut_at_a_word_boundary_within_forty_characters():
    words = " ".join(["A" + "a" * 8, "B" + "b" * 8, "C" + "c" * 8, "D" + "d" * 9])
    assert len(words) == 40
    assert F.cut(words) == words
    assert F.cut(words + " Eeee") == words
    assert F.cut("Photos are the input.") == "Photos are the input"
    assert F.cut("Photos are the input. Every clip starts there") == "Photos are the input. Every clip starts"
    assert F.cut("Become the default dental clinic here — then expand") == "Become the default dental clinic here"
    assert F.cut("x" * 50) == "x" * 40
    assert F.cut("  many   words\nand\tbreaks ") == "many words and breaks"


def test_a_long_product_reason_names_its_initiatives_or_counts_them_when_they_would_not_fit():
    segment_name = "S" + "e" * 39  # a 40-character label, the longest a segment's label can be
    initiatives = [
        {
            "id": "b%d" % n,
            "statement": "Statement",
            "name": ("Initiative %d " % n).ljust(40, "x"),
            "segment": segment_name,
        }
        for n in range(1, 6)
    ]
    four = F.build({"segments": [segment_name], "channels": [], "initiatives": initiatives[:4], "industry": []})
    reason = by_title(four, "%s Product Owner" % segment_name)["reason"]
    assert "“Initiative 1 " in reason and len(reason) <= D.LIMITS["reason"]
    five = F.build({"segments": [segment_name], "channels": [], "initiatives": initiatives, "industry": []})
    reason = by_title(five, "%s Product Owner" % segment_name)["reason"]
    assert reason == "Owns the %s segment's product and 5 initiatives (b1, b2, b3, b4, b5)." % segment_name
    assert len(reason) <= D.LIMITS["reason"]


def test_a_product_reason_quotes_its_labels_curly_and_names_a_repeated_label_once():
    bets = [
        {"id": "b1", "statement": "Run live rooms", "name": "Live rooms", "segment": "Creators"},
        {"id": "b2", "statement": "Host live rooms weekly", "name": "Live rooms", "segment": "Creators"},
        {"id": "b3", "statement": "Start fan clubs", "name": "Fan clubs", "segment": "Creators"},
    ]
    result = F.build({"segments": ["Creators"], "channels": [], "initiatives": bets, "industry": []})
    assert by_title(result, "Creators Product Owner")["reason"] == (
        "Owns the Creators segment's product and the “Live rooms” and “Fan clubs” initiatives (b1, b2, b3)."
    )


# ---------------------------------------------------------------------------------------------- channels
def test_unknown_channels_are_dropped_and_the_picked_ones_follow_the_list_order():
    seats = F.build({"segments": [], "channels": ["events", "bogus", "seo"], "initiatives": [], "industry": []})
    assert [s["title"] for s in seats["employees"] if s["title"] in ("SEO Owner", "Events Owner")] == [
        "SEO Owner",
        "Events Owner",
    ]


def test_no_picked_channel_is_the_default_list():
    default = [
        "Marketing Team Manager",
        "Conversion Rate Optimization Owner",
        "SEO Owner",
        "Social Ads Owner",
        "Search Ads Owner",
    ]
    for picked in ([], ["bogus"]):
        seats = F.build({"segments": [], "channels": picked, "initiatives": [], "industry": []})
        assert [s["title"] for s in seats["employees"] if s["team"] == "Marketing"] == default


# ---------------------------------------------------------------------------------------------- coverage and removal
def test_a_removed_owner_leaves_its_things_uncovered_and_is_not_in_the_chart():
    result = F.build(HOTMIC, ["creators-product-owner"])
    assert "creators-product-owner" not in [seat["id"] for seat in result["employees"]]
    assert result["uncovered"] == ["Creators product", "Live rooms"]


def test_a_removed_manager_leaves_its_team_uncovered_and_its_reports_move_up_the_line():
    result = F.build(HOTMIC, ["engineering-manager"])
    assert result["uncovered"] == ["Engineering"]
    assert all(seat["reports_to"] != "engineering-manager" for seat in result["employees"])
    assert by_title(result, "Builder")["reports_to"] is None


def test_a_removed_marketing_owner_leaves_only_its_segment_uncovered():
    assert F.build(HOTMIC, ["creators-marketing-owner"])["uncovered"] == ["Creators marketing"]


@pytest.mark.parametrize(
    "seat, name",
    [("security-owner", "Security"), ("infrastructure-owner", "Infrastructure"), ("pipeline-owner", "Pipeline")],
)
def test_a_removed_factory_owner_leaves_its_area_uncovered(seat, name):
    assert F.build(HOTMIC, [seat])["uncovered"] == [name]


def test_removed_is_a_list_of_seat_ids_and_a_string_is_refused():
    with pytest.raises(TypeError):
        F.build(HOTMIC, "creators-product-owner")


# ---------------------------------------------------------------------------------------------- industry
def test_industry_owners_are_kept_up_to_three_and_their_topic_reads_in_the_reason():
    industry = [
        {"title": "Scheduling Owner", "template": "scheduling-bookings"},
        {"title": "Insurance Billing Owner", "template": "billing"},
        {"title": "HR Owner", "template": "people-lead"},
        {"title": "Court Filings Owner", "template": "paralegal"},
        {"title": "Made Up Owner", "template": "not-a-card"},
    ]
    seats = F.build({"segments": [], "channels": [], "initiatives": [], "industry": industry})["employees"]
    operations = [seat for seat in seats if seat["team"] == "Operations"]
    assert [seat["title"] for seat in operations] == ["Scheduling Owner", "Insurance Billing Owner", "HR Owner"]
    assert [seat["reason"] for seat in operations] == [
        "Owns scheduling for the company.",
        "Owns insurance billing for the company.",
        "Owns HR for the company.",
    ]
    assert [seat["department"] for seat in operations] == ["operations", "finance", "hr"]


def test_an_industry_owner_takes_no_built_in_card():
    built_in = {role["slug"] for role in D.ROLES if role["core"]}
    assert {"coo", "botops", "goal-manager", "librarian"} <= built_in
    assert not (F.INDUSTRY_TEMPLATES & built_in)
    industry = [{"title": "%s Owner" % slug.title(), "template": slug} for slug in sorted(built_in)]
    seats = F.build({"segments": [], "channels": [], "initiatives": [], "industry": industry})["employees"]
    assert [seat for seat in seats if seat["team"] == "Operations"] == []


# ---------------------------------------------------------------------------------------------- vocabulary and cards
def test_the_formula_channels_are_the_vocabulary_channels_in_the_same_order():
    assert D.CHANNELS == tuple(channel["key"] for channel in F.FORMULA["channels"])


def test_every_template_is_a_card_or_one_of_the_six_new_ones():
    used = {seat["template"] for team in F.FORMULA["teams"] for seat in team["seats"] if "template" in seat}
    used |= {channel["template"] for channel in F.FORMULA["channels"]}
    assert used - set(CARDS) <= NEW_TEMPLATES
    assert NEW_TEMPLATES <= used


def test_the_stand_ins_go_once_the_six_cards_are_in_roles_json():
    assert set(STAND_IN) <= NEW_TEMPLATES
    assert not (NEW_TEMPLATES <= set(CARDS) and STAND_IN), "all six new cards exist in roles.json: delete STAND_IN"


def test_a_seat_on_a_card_carries_the_cards_department():
    for case in CASES.values():
        for seat in F.build(case["inputs"], case.get("removed", ()))["employees"]:
            if seat["template"] in CARDS:
                assert seat["department"] == CARDS[seat["template"]], seat["title"]


def test_inputs_from_draft_reads_the_picked_segments_channels_and_bets():
    draft = {
        "strategy": {
            "channels": ["seo"],
            "market": {"segments": [{"name": "Creators", "ours": True}, {"name": "Fans", "ours": False}]},
            "bets": [{"id": "b1", "statement": "Run live rooms", "name": "Live rooms", "segment": "Creators"}],
        }
    }
    industry = [{"title": "Scheduling Owner", "template": "scheduling-bookings"}]
    assert F.inputs_from_draft(draft, industry=industry) == {
        "segments": ["Creators"],
        "channels": ["seo"],
        "initiatives": [{"id": "b1", "statement": "Run live rooms", "name": "Live rooms", "segment": "Creators"}],
        "industry": industry,
    }


def test_a_blank_draft_is_the_default_channels_and_no_segment_or_bet_seats():
    inputs = F.inputs_from_draft(D.blank())
    assert inputs["segments"] == [] and inputs["initiatives"] == []
    assert inputs["channels"] == list(F.DEFAULT_CHANNELS)
    result = F.build(inputs)
    assert len(result["employees"]) == 21
    assert result["uncovered"] == []


# ---------------------------------------------------------------------------------------------- read back
@pytest.mark.parametrize("name", sorted(CASES))
def test_the_rows_read_back_through_the_company_file_reader(name):
    rows, draft = readback(CASES[name])
    assert [bot["id"] for bot in draft["bots"]] == [row["id"] for row in rows]
    bots = {bot["id"]: bot for bot in draft["bots"]}
    for bot, row in zip(draft["bots"], rows):
        assert (bot["title"], bot["team"], bot["dept"], bot["reason"]) == (
            row["title"],
            row["team"],
            row["department"],
            row["reason"],
        )
        assert bot["template"] == row["template"] and bot["start"] is True
        # The setup draft allows one first job, so an initiative Owner names its bet in its reason instead.
        assert "first_job" not in bot
        if row["reports_to"]:
            assert D.manager_of(draft, bot) == row["reports_to"], row["title"]
        else:
            # No manager in the chart: the company file names none, and the app gives the seat its default manager.
            assert not bot.get("reports_to")
            manager = D.manager_of(draft, bot)
            assert manager == "chris" or bots[manager]["dept"] == bot["dept"], row["title"]


def test_the_read_back_gives_each_seat_without_a_manager_the_app_default():
    _, draft = readback(CASES["hotmic"])
    managers = {bot["title"]: D.manager_of(draft, bot) for bot in draft["bots"]}
    assert managers["Partnerships Owner"] == "sales-manager"  # the sales department's lead
    assert managers["Finance Owner"] == "chris"  # the finance department has no lead in the chart


def test_an_industry_seat_with_no_manager_lands_under_its_department_lead_or_the_owner():
    _, draft = readback(CASES["dental_clinic"])
    managers = {bot["title"]: D.manager_of(draft, bot) for bot in draft["bots"]}
    assert managers["Scheduling Owner"] == "chris"  # operations has no lead in the chart, so the owner
    assert managers["Insurance Billing Owner"] == "finance-owner"  # the finance department's lead


# ---------------------------------------------------------------------------------------------- the contract
def test_channels_are_kept_in_order_without_repeats_and_absent_when_none_is_picked():
    assert strategy_draft(channels=["seo", "cro", "seo"])["strategy"]["channels"] == ["seo", "cro"]
    assert "channels" not in strategy_draft(channels=[])["strategy"]


def test_a_channel_must_be_on_the_list_and_a_repeat_does_not_count_toward_the_cap():
    with pytest.raises(D.DraftError, match="channel"):
        strategy_draft(channels=["seo", "billboards"])
    with pytest.raises(D.DraftError, match="list"):
        strategy_draft(channels="seo")
    assert strategy_draft(channels=["cro"] * 12)["strategy"]["channels"] == ["cro"]
    assert strategy_draft(channels=list(D.CHANNELS))["strategy"]["channels"] == list(D.CHANNELS)


def test_a_bet_name_is_trimmed_blank_is_absent_and_longer_than_forty_is_refused():
    bets = strategy_draft(bets=[bet(1, "Run live rooms", name="  Live rooms  "), bet(2, "Fan clubs", name="   ")])[
        "strategy"
    ]["bets"]
    assert bets[0]["name"] == "Live rooms"
    assert "name" not in bets[1]
    assert strategy_draft(bets=[bet(1, "Run", name="a" * 40)])["strategy"]["bets"][0]["name"] == "a" * 40
    with pytest.raises(D.DraftError, match="name"):
        strategy_draft(bets=[bet(1, "Run live rooms", name="a" * 41)])


def test_a_bet_segment_matches_a_segment_name_ignoring_case_and_spacing_and_a_stranger_is_dropped():
    draft = strategy_draft(
        market=market({**segment("Creators"), "name": "Creators "}, segment("Fans")),
        bets=[bet(1, "Run live rooms", segment="  creators "), bet(2, "Photos are the input", segment="Teachers")],
    )
    assert draft["strategy"]["bets"][0]["segment"] == "Creators "
    assert "segment" not in draft["strategy"]["bets"][1]


def test_a_bet_segment_longer_than_a_segment_name_can_be_is_dropped_not_refused():
    name = "a" * 80  # the longest a segment's name can be
    draft = strategy_draft(
        market=market({**segment("Long"), "name": name}),
        bets=[bet(1, "Run", segment=name), bet(2, "Run", segment=name + "a")],
    )
    assert draft["strategy"]["bets"][0]["segment"] == name
    assert "segment" not in draft["strategy"]["bets"][1]


def test_the_new_keys_round_trip_through_the_company_file():
    draft = strategy_draft(
        market=market(segment("Creators"), segment("Fans")),
        channels=["events", "seo"],
        bets=[bet(1, "Run live rooms", name="Live rooms", segment="Creators"), bet(2, "Photos are the input")],
    )
    text = yamlfile.to_yaml(draft)
    written = yaml.safe_load(text)["strategy"]
    assert written["channels"] == ["events", "seo"]
    assert written["bets"][0]["name"] == "Live rooms" and written["bets"][0]["segment"] == "Creators"
    assert "name" not in written["bets"][1] and "segment" not in written["bets"][1]
    back = yamlfile.from_yaml(text)["strategy"]
    assert back["channels"] == ["events", "seo"]
    assert back["bets"][0]["name"] == "Live rooms" and back["bets"][0]["segment"] == "Creators"
    assert "name" not in back["bets"][1] and "segment" not in back["bets"][1]


def test_a_file_without_the_new_keys_is_written_and_read_as_before():
    draft = strategy_draft(market=market(segment("Creators")), bets=[bet(1, "Photos are the input")])
    text = yamlfile.to_yaml(draft)
    strategy = yaml.safe_load(text)["strategy"]
    assert "channels" not in strategy
    assert "name" not in strategy["bets"][0] and "segment" not in strategy["bets"][0]
    back = yamlfile.from_yaml(text)
    assert "channels" not in back["strategy"]
    assert back["strategy"]["bets"][0]["statement"] == "Photos are the input"


def test_the_reader_refuses_a_channel_the_list_does_not_have():
    text = yamlfile.to_yaml(strategy_draft(channels=["seo"]))
    with pytest.raises(D.DraftError):
        yamlfile.from_yaml(text.replace("channels: [seo]", "channels: [billboards]"))


# ---------------------------------------------------------------------------------------------- helpers for the contract
def segment(name):
    return {
        "key": name.lower(),
        "name": name,
        "parent": None,
        "usd": None,
        "method": "top_down",
        "arithmetic": "",
        "evidence": [],
        "ours": True,
        "next": False,
    }


def market(*segments):
    return {**D.blank_strategy()["market"], "segments": list(segments)}


def bet(number, statement, **extra):
    return {
        "id": "b%d" % number,
        "statement": statement,
        "why": "",
        "rules_out": "",
        "wrong_if": "",
        "check_by": "",
        **extra,
    }


def strategy_draft(**fields):
    """A valid draft whose strategy holds `fields`; the rest of the strategy is blank."""
    draft = D.blank()
    draft["strategy"] = {**D.blank_strategy(), **fields}
    return D.check(draft)
