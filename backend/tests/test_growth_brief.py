"""The channel read, the segment read and the channel alert (backend/growth_brief.py, task #288). Acme, a home-cleaning
company, runs SEO with its SEO bot (`ops` here) as owner. Each test names the spec criterion it checks."""

import time
from datetime import date, timedelta

from backend import growth_brief as B
from backend import hubdb as H
from backend.tests.helpers.api import get, headers, post, put
from backend.tests.helpers.company import bot


def patch(api, path, body, token="ana-test", expected=200):
    r = api.patch("/api/v2/" + path, json=body, headers=headers(token))
    assert r.status_code == expected, r.text
    return r.json()


def seo(api, owner="ops", target=True, **more):
    """Channel `seo` owned by the bot, its KPI linked to a goal (green at 10,000+, red under 9,000)."""
    kpi = post(api, "kpis", {"name": "Organic clicks", "unit": "", "cadence": "monthly"})["kpi"]
    if target:
        goal = post(api, "goals", {"title": "Grow organic traffic", "owner": "company"})["goal"]
        post(api, f"goals/{goal['id']}/kpis", {"kpi_id": kpi["id"], "kind": "maintain", "min": 9000})
    body = {"slug": "seo", "name": "SEO", "stage": "scaling", "budget_month": 2000, "input_kpi_id": kpi["id"], **more}
    if owner:
        body["owner_ref"] = owner
    post(api, "growth/channels", body)
    return kpi


def log(api, kpi, value, ago=1):
    """A reading for the day `ago` days back: a weekly KPI read today stays fresh, whatever the day the suite runs."""
    day = (date.today() - timedelta(days=ago)).isoformat()
    post(api, f"kpis/{kpi['id']}/readings", {"value": value, "period_end": f"{day}T00:00:00Z"})


def source(api, system="Billing export"):
    """A file source that fills the channel's spend from its `cost` column, set as the channel's source."""
    body = {"truth_for": "ad spend", "system": system, "sync_kind": "file", "sync_map": {"fields": {"spend": "cost"}}}
    src = post(api, "growth/sources", body)
    patch(api, "growth/channels/seo", {"source_id": src["id"]})
    return src


def push(api, src, text):
    return post(api, f"growth/sources/{src['id']}/rows", {"csv": text})


def fail(api, src, error="403 from the export"):
    from backend import growth_sync as GS

    with api.app.state.store.transaction() as c:
        GS.run(c, "human:ana", H._one(c, "SELECT * FROM gtm_sources WHERE id=?", (src["id"],)), error=error)


def months(api, rows):
    for month, fields in rows.items():
        put(api, "growth/cohorts", {"channel": "seo", "month": month, "quality": "actual", **fields})


def alert_tasks(api, kind=None):
    with api.app.state.store.read() as c:
        rows = H._rows(
            c.execute(
                "SELECT x.kind, x.told, x.task_id, t.status, t.owner_id, t.requester_id, t.title FROM growth_crossings x "
                "LEFT JOIN tasks t ON t.id=x.task_id ORDER BY x.at, x.rowid"
            )
        )
    return [r for r in rows if kind is None or r["kind"] == kind]


def labels(api, task_id):
    with api.app.state.store.read() as c:
        return sorted(
            r[0]
            for r in c.execute(
                "SELECT tg.key FROM task_tags tt JOIN tags tg ON tg.id=tt.tag_id WHERE tt.task_id=?", (task_id,)
            )
        )


def crossed(monkeypatch):
    """Every growth.channel.crossed event the checks emit, as its data."""
    heard = []
    real = H.hook

    def hook(conn, type, subject, data, actor, revision=None):
        if type == B.EVENT:
            heard.append(data())
        return real(conn, type, subject, data, actor, revision)

    monkeypatch.setattr(H, "hook", hook)
    return heard


def back_a_day(api):
    """Every crossing told yesterday: what a day passing looks like to the once-a-day rule."""
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE growth_crossings SET at='2000-01-01T00:00:00.000000Z'")


# ----------------------------------------------------------------------------- the read
def test_ac1_ac2_the_owner_reads_kpi_months_changes_tests_and_sources_in_one_call(api):
    ops = bot(api, "ops")
    kpi = seo(api)
    log(api, kpi, 8100)
    months(
        api,
        {
            "2026-09": {"spend": 2000, "new_customers": 48, "kept_d30": 64},
            "2026-10": {"spend": 2000, "new_customers": 41, "kept_d30": 62},
        },
    )
    out = get(api, "growth/channels/seo", token=ops)
    assert list(out)[: len(out)] and {"kpi", "months", "changed", "tests", "sources", "guardrail"} <= set(out)
    k = out["kpi"]
    assert (k["name"], k["value"], k["colour"]) == ("Organic clicks", 8100, "red") and "9,000" in k["target"]
    assert [m["month"] for m in out["months"]] == ["2026-10", "2026-09"]
    # The text view prints KPI, months, changes, tests and sources, in that order (AC-1).
    from clients import remotecli

    text = remotecli.growth_text("growth channel show", out)
    order = [text.index(word) for word in ("KPI", "Month", "Changed", "Tests", "Sources")]
    assert order == sorted(order), text
    assert "RED" in text and "8,100" in text


def test_ac2_the_read_stays_under_a_second_at_24_months_20_channels_200_tasks(api):
    seo(api)
    for i in range(19):
        post(api, "growth/channels", {"slug": f"ch-{i}", "name": f"Channel {i}"})
    for n in range(24):
        y, m = 2025 + (n // 12), n % 12 + 1
        put(api, "growth/cohorts", {"channel": "seo", "month": f"{y}-{m:02d}", "spend": 100 + n, "new_customers": 9})
    with api.app.state.store.transaction() as c:
        for i in range(200):
            H.task_create(
                c, "human:ana", f"Idea {i}", "", "human:ana", labels=["channel:seo"], deduplicate=False, lint=False
            )
    started = time.perf_counter()
    out = get(api, "growth/channels/seo")
    assert time.perf_counter() - started < 1.0
    assert len(out["months"]) == 6


def test_ac3_ac4_no_kpi_says_how_to_link_one_and_no_target_says_whose_it_is(api):
    post(api, "growth/channels", {"slug": "seo", "name": "SEO"})
    k = get(api, "growth/channels/seo")["kpi"]
    assert k["linked"] is False and "owns" in k["who_may"]
    # The fix the read gives is a command the CLI takes (the flag is shared with the Growth line).
    from clients import hubcli

    parsed = hubcli.parser().parse_args(k["fix"].replace("<id>", "kpi1").split()[1:])
    assert parsed.input_kpi_id == "kpi1"
    kpi = post(api, "kpis", {"name": "Organic clicks"})["kpi"]
    patch(api, "growth/channels/seo", {"input_kpi_id": kpi["id"]})
    log(api, kpi, 8100)
    k = get(api, "growth/channels/seo")["kpi"]
    assert (
        k["value"] == 8100 and k["colour"] is None and k["text"] == "no target: set on the goal by the Strategy Owner"
    )


def test_ac5_the_owner_bot_links_its_kpi_and_another_bot_is_refused(api):
    ops, finance = bot(api, "ops"), bot(api, "finance")
    post(api, "growth/channels", {"slug": "seo", "name": "SEO", "owner_ref": "ops"})
    kpi = post(api, "kpis", {"name": "Organic clicks"})["kpi"]
    assert patch(api, "growth/channels/seo", {"input_kpi_id": kpi["id"]}, token=ops)["input_kpi_id"] == kpi["id"]
    refused = patch(api, "growth/channels/seo", {"input_kpi_id": None}, token=finance, expected=403)
    assert "owner" in str(refused).lower()


def test_ac6_ac7_each_figure_shows_its_change_and_a_missing_one_is_not_recorded(api):
    seo(api)
    months(
        api,
        {
            "2026-09": {"spend": 2000, "new_customers": 48},
            "2026-10": {"spend": 2000, "new_customers": 41, "kept_d30": 62},
        },
    )
    patch(api, "growth/channels/seo", {"budget_month": 2500})
    ch = get(api, "growth/channels/seo")["changed"]
    assert (ch["month"], ch["before"]) == ("2026-10", "2026-09")
    new = next(f for f in ch["figures"] if f["figure"] == "new_customers")
    assert (new["value"], new["before"], new["diff"], new["pct"]) == (41, 48, -7, -14.6)
    kept = next(f for f in ch["figures"] if f["figure"] == "kept_d30")
    assert kept["recorded"] is False and kept["diff"] is None and kept["note"] == "not recorded"  # never 0 (AC-7)
    budget = next(e for e in ch["events"] if e["kind"] == "budget")
    assert budget["by"] == "human:ana" and budget["at"] and "because" not in budget["text"]


def test_ac8_tests_the_reader_may_see_with_stage_start_and_result(api):
    seo(api)
    with api.app.state.store.transaction() as c:
        running = H.task_create(
            c,
            "human:ana",
            "Comparison pages",
            "",
            "human:ana",
            labels=["channel:seo", "growth:running"],
            deduplicate=False,
            lint=False,
        )
        hidden = H.task_create(
            c,
            "human:ben",
            "Secret test",
            "",
            "human:ben",
            private=True,
            labels=["channel:seo", "growth:running"],
            deduplicate=False,
            lint=False,
        )
    tests = get(api, "growth/channels/seo", token="cara-test")["tests"]
    titles = [t["title"] for t in tests["open"]]
    assert "Comparison pages" in titles and "Secret test" not in titles
    first = next(t for t in tests["open"] if t["title"] == "Comparison pages")
    assert first["stage"] == "testing" and first["started"]
    assert running and hidden


def test_ac9_a_failing_source_marks_its_figures_stale_with_the_last_good_date(api):
    seo(api)
    src = source(api)
    push(api, src, "month,channel,cost\n2026-10,seo,2000\n")
    fail(api, src)
    s = get(api, "growth/channels/seo")["sources"]["sources"][0]
    assert s["stale"] is True and s["last_good"] and s["health"] == "failing" and s["fills"] == ["spend"]


# ----------------------------------------------------------------------------- what lines up with a change
def test_ac20_a_change_lists_the_test_in_its_window_and_a_stale_source_as_a_data_gap(api):
    seo(api)
    src = source(api)
    months(api, {"2026-09": {"new_customers": 48}, "2026-10": {"new_customers": 41}})
    push(api, src, "month,channel,cost\n2026-09,seo,2000\n2026-10,seo,2009\n")
    fail(api, src)
    with api.app.state.store.transaction() as c:
        tid = H.task_create(
            c,
            "human:ana",
            "FAQ schema",
            "",
            "human:ana",
            labels=["channel:seo", "growth:running"],
            deduplicate=False,
            lint=False,
        )
        assert tid
    out = get(api, "growth/channels/seo")
    cac = next(f for f in out["changed"]["figures"] if f["figure"] == "cac")
    assert cac["data_gap"] and "may be a data gap" in cac["data_gap"][0]
    assert any("FAQ schema" in e["text"] for e in cac["beside"]), cac
    assert "because" not in str(cac) and cac["unexplained"] is False


def test_ac21_a_change_with_nothing_in_its_window_says_nothing_recorded_explains_it(api):
    seo(api)
    months(api, {"2026-09": {"new_customers": 48}, "2026-10": {"new_customers": 41}})
    new = next(f for f in get(api, "growth/channels/seo")["changed"]["figures"] if f["figure"] == "new_customers")
    assert new["unexplained"] is True and new["note"] == "nothing recorded on this channel explains it"


# ----------------------------------------------------------------------------- the alert
def test_ac10_ac24_going_red_files_one_labelled_task_for_the_owner_and_one_event(api):
    bot(api, "ops")
    kpi = seo(api)
    log(api, kpi, 10500, 9)
    assert alert_tasks(api) == []
    log(api, kpi, 8100, 4)
    rows = alert_tasks(api, "kpi")
    assert len(rows) == 1 and rows[0]["told"] == "task"
    task = rows[0]
    assert task["owner_id"] == "bot:ops" and task["requester_id"] == "bot:ops"
    assert "SEO" in task["title"] and "Organic clicks" in task["title"] and "8,100" in task["title"]
    assert labels(api, task["task_id"]) == ["channel:seo", "growth:alert"]
    with api.app.state.store.read() as c:
        body = H.task_request_text(c, task["task_id"])  # a task's request is its first message
    assert "tico growth channel show seo" in body
    # AC-24: the channel read lists it.
    out = get(api, "growth/channels/seo")
    assert [a["task_id"] for a in out["alerts"]] == [task["task_id"]]
    assert task["task_id"] in [t["task_id"] for t in out["tasks"]]


def test_ac11_still_red_the_same_day_adds_nothing_and_the_next_day_one_comment(api, monkeypatch):
    bot(api, "ops")
    kpi = seo(api)
    heard = crossed(monkeypatch)
    log(api, kpi, 10500, 9)
    log(api, kpi, 8100, 5)
    log(api, kpi, 8000, 4)
    assert [r["told"] for r in alert_tasks(api)] == ["task"]
    back_a_day(api)
    log(api, kpi, 7900, 3)
    log(api, kpi, 7800, 2)
    rows = alert_tasks(api)
    assert [r["told"] for r in rows] == ["task", "comment"]
    # The daily note is no crossing: no second event, and it says it is still over.
    assert len(heard) == 1 and _said(api, rows[0]["task_id"])[0].startswith("Still over the line:")


def test_ac12_back_to_green_comments_once_asks_to_close_and_leaves_it_open(api):
    bot(api, "ops")
    kpi = seo(api)
    log(api, kpi, 10500, 9)
    log(api, kpi, 8100, 5)
    log(api, kpi, 10600, 4)
    rows = alert_tasks(api, "kpi")
    assert [r["told"] for r in rows] == ["task", "comment"]
    tid = rows[0]["task_id"]
    with api.app.state.store.read() as c:
        assert H.task(c, tid)["status"] == "open"
        said = [m["body"] for m in H.task_comments(c, tid)]
    assert any("back to green" in s and "Close this task" in s for s in said)


def test_ac13_the_guardrail_going_over_its_max_files_a_task(api):
    bot(api, "ops")
    refunds = post(api, "kpis", {"name": "Refund rate", "unit": "%", "cadence": "monthly"})["kpi"]
    seo(api, guardrail_kpi_id=refunds["id"], guardrail_max=5)
    log(api, refunds, 3, 9)
    log(api, refunds, 7, 4)
    rows = alert_tasks(api, "guardrail")
    assert len(rows) == 1 and rows[0]["told"] == "task" and "Refund rate" in rows[0]["title"]


def test_ac14_no_owner_tells_nobody_and_the_read_says_so(api, monkeypatch):
    kpi = seo(api, owner=None)
    heard = crossed(monkeypatch)
    log(api, kpi, 10500, 9)
    log(api, kpi, 8100, 4)
    log(api, kpi, 8000, 3)  # still red: not a crossing, not recorded
    rows = alert_tasks(api)
    assert [(r["told"], r["task_id"]) for r in rows] == [("none", None)]
    log(api, kpi, 10500, 2)
    log(api, kpi, 8100, 1)
    # Each crossing is recorded for the read; the event goes out once a day.
    assert [r["told"] for r in alert_tasks(api)] == ["none", "none", "none"] and len(heard) == 1
    months(api, {"2026-11": {"new_customers": 3}})
    events = get(api, "growth/channels/seo")["changed"]["events"]
    assert any("no owner: nobody was told" in e["text"] for e in events)


def test_ac15_a_crossing_emits_the_event_once_with_ids_only(api, monkeypatch):
    bot(api, "ops")
    kpi = seo(api)
    heard = []
    real = H.hook

    def hook(conn, type, subject, data, actor, revision=None):
        if type == B.EVENT:
            heard.append(data())
        return real(conn, type, subject, data, actor, revision)

    monkeypatch.setattr(H, "hook", hook)
    log(api, kpi, 10500, 9)
    log(api, kpi, 8100, 4)
    assert len(heard) == 1 and heard[0]["kind"] == "kpi" and heard[0]["kpi_id"] == kpi["id"]
    assert set(heard[0]) == {"channel_id", "kind", "kpi_id", "source_id", "crossing_id"}


def test_ac22_red_closed_red_again_files_a_new_task(api):
    bot(api, "ops")
    kpi = seo(api)
    log(api, kpi, 10500, 9)
    log(api, kpi, 8100, 8)
    first = alert_tasks(api, "kpi")[0]["task_id"]
    with api.app.state.store.transaction() as c:
        H.task_close(c, "bot:ops", first, "Dealt with.", H.task(c, first)["version"])
    log(api, kpi, 10500, 7)
    log(api, kpi, 8000, 6)
    rows = [r for r in alert_tasks(api, "kpi") if r["told"] == "task"]
    assert len(rows) == 2 and rows[1]["task_id"] != first and rows[1]["status"] == "open"


def test_ac23_a_failing_source_files_a_task_naming_it(api):
    bot(api, "ops")
    seo(api)
    src = source(api)
    push(api, src, "month,channel,cost\n2026-10,seo,2000\n")
    fail(api, src)
    rows = alert_tasks(api, "source")
    assert len(rows) == 1 and rows[0]["told"] == "task" and "Billing export" in rows[0]["title"]


def test_a_failing_alert_check_never_undoes_the_reading(api, monkeypatch):
    kpi = seo(api)

    def boom(*a, **k):
        raise RuntimeError("broken")

    monkeypatch.setattr(B, "check_channel", boom)
    log(api, kpi, 8100)
    assert get(api, f"kpis/{kpi['id']}")["kpi"]["latest"]["value"] == 8100


# ----------------------------------------------------------------------------- the segment read
def test_ac16_a_segment_shows_six_months_and_one_under_the_minimum_counts_only(api, monkeypatch):
    from backend import growth_computed as GX
    from backend.tests.helpers.growth_computed import TODAY, as_csv, customer

    monkeypatch.setattr(GX, "today", lambda conf: TODAY)
    rows = [customer(i, "2026-04-03", "seo", segment="agency") for i in range(40)]
    rows += [customer(100 + i, "2026-05-03", "seo", segment="agency") for i in range(40)]
    rows += [customer(200 + i, "2026-05-04", "seo", segment="startup") for i in range(12)]
    post(api, "growth/channels", {"slug": "seo", "name": "SEO"})
    post(api, "growth/customers", {"csv": as_csv(rows)})
    agency = get(api, "growth/segments/agency")
    assert agency["spend"] == "spend is not split by segment" and agency["counts_only"] is False
    assert [m["month"] for m in agency["months"]] == ["2026-05", "2026-04"]
    assert {f["figure"] for f in agency["changed"]["figures"]} == {"new_customers", "kept_d30", "kept_d90", "ltv"}
    startup = get(api, "growth/segments/startup")
    assert startup["counts_only"] is True and set(startup["months"][0]) == {"month", "too_few", "new_customers"}
    get(api, "growth/segments/nobody", expected=404)


def test_ac18_each_view_read_is_counted_by_caller(api):
    ops = bot(api, "ops")
    seo(api)
    get(api, "growth/channels/seo", token=ops)
    get(api, "growth/channels/seo", token=ops)
    reads = get(api, "growth/view-reads")["reads"]
    assert {"actor": "bot:ops", "view": "channel", "reads": 2} in [
        {k: r[k] for k in ("actor", "view", "reads")} for r in reads
    ]


# ----------------------------------------------------------------------------- templates
def test_ac17_channel_and_segment_owners_read_their_view_each_run_and_split_nothing_by_hand():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "templates" / "catalog"
    for name in ("seo", "content", "email", "cro", "search-ads", "social-ads", "affiliates", "app-store"):
        text = (root / f"{name}-owner" / "AGENTS.md").read_text()
        assert "At the start of each run, read your channel with `tico growth channel show <slug>`" in text, name
        assert "growth:alert" in text, name
    segment = (root / "segment-marketing-owner" / "AGENTS.md").read_text()
    assert "read your segment with `tico growth segment show <label>`" in segment
    assert "so you split them" not in segment and "never split them by hand" in segment


# ----------------------------------------------------------------------------- the edge of a line (Review 1)
def _said(api, tid):
    """The notes on an alert task after its request (a task's request is its first message)."""
    with api.app.state.store.read() as c:
        request = H.task_request(c, tid)
        return [m["body"] for m in H.task_comments(c, tid) if not request or m.get("id") != request["id"]]


def test_a_kpi_flapping_red_and_green_all_day_files_one_task_and_one_back_note(api, monkeypatch):
    bot(api, "ops")
    kpi = seo(api)
    heard = []
    real = H.hook

    def hook(conn, type, subject, data, actor, revision=None):
        if type == B.EVENT:
            heard.append(data())
        return real(conn, type, subject, data, actor, revision)

    monkeypatch.setattr(H, "hook", hook)
    log(api, kpi, 10500, 9)
    for ago in (8, 7, 6, 5, 4, 3, 2):
        log(api, kpi, 8100 if ago % 2 == 0 else 10500, ago)
    told = [r for r in alert_tasks(api, "kpi") if r["told"] in ("task", "comment")]
    assert [r["told"] for r in told] == ["task", "comment"], told
    assert len(_said(api, told[0]["task_id"])) == 1 and len(heard) == 2
    assert len(alert_tasks(api, "kpi")) == 7  # every crossing is still recorded for the read, told or not
    back_a_day(api)
    log(api, kpi, 8000, 1)  # the next day, red again: one note
    assert len(_said(api, told[0]["task_id"])) == 2


def test_red_to_yellow_is_not_back_and_says_nothing(api):
    bot(api, "ops")
    kpi = seo(api)
    log(api, kpi, 10500, 9)
    log(api, kpi, 8100, 5)
    log(api, kpi, 9500, 4)  # yellow: under target, above the floor
    log(api, kpi, 8100, 3)
    rows = alert_tasks(api, "kpi")
    assert [r["told"] for r in rows] == ["task"] and _said(api, rows[0]["task_id"]) == []


def test_a_source_failing_and_recovering_all_day_files_one_task_and_one_back_note(api):
    bot(api, "ops")
    seo(api)
    src = source(api)
    for _ in range(4):
        fail(api, src)
        push(api, src, "month,channel,cost\n2026-10,seo,2000\n")
    told = [r for r in alert_tasks(api, "source") if r["told"] in ("task", "comment")]
    assert [r["told"] for r in told] == ["task", "comment"], told
    assert len(_said(api, told[0]["task_id"])) == 1


def test_after_the_owner_changes_the_next_crossing_files_a_task_for_the_new_owner(api):
    bot(api, "ops")
    bot(api, "finance")
    kpi = seo(api)
    log(api, kpi, 10500, 9)
    log(api, kpi, 8100, 8)
    first = alert_tasks(api, "kpi")[0]["task_id"]
    patch(api, "growth/channels/seo", {"owner_ref": "finance"})
    log(api, kpi, 10500, 7)
    back_a_day(api)
    log(api, kpi, 8000, 6)
    tasks = [r for r in alert_tasks(api, "kpi") if r["told"] == "task"]
    assert len(tasks) == 2 and tasks[1]["task_id"] != first and tasks[1]["owner_id"] != tasks[0]["owner_id"]
