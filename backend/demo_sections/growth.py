"""Growth: the numbers page for Acme Lights' season.

One story over eight weeks, from one daily model, so every figure adds up: a week's KPI reading is the sum of the same
days the month rows add up. Each Monday the marketing lead's "Weekly numbers" routine sets the month rows through the
Sunday before and logs the week's readings. Those runs are history, so they are written as the runs would have left
them, played through the engine as the scheduler fires them: each run's bot turn opens the routine's task, and the turn's end charges the run. Creator videos drive store sales and scale up. Paid ads cost more
per unit than a unit earns, so they pause, and their test closes as Failed.

The growth model counts revenue per customer per month. A unit's sale is spread over the months it counts (a $299 unit
is about $25 a month over a year, and a Scene Library plan adds about $6), so each channel's monthly revenue stands for
one sale. The report says so.
"""

import json
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from .. import automations, goals, growth, hubdb, marketing_tasks, models, providers, routines
from ..demo_seed import TOKENS
from ..scheduler import next_due, stamp

ZONE = ZoneInfo("America/Los_Angeles")
UNKNOWN = growth.UNKNOWN

CONTENT = {
    "unit": "units",
    "settings": {"revenue_month": 31, "margin_pct": 46},
    "weeks": 8,
    "weekday": (0.9, 0.9, 0.95, 1.0, 1.15, 1.35, 1.25),       # units by day of the week, Monday first
    "unknown_share": 0.04,                                    # sales with no source tag, as a share of the day's units
    "unknown_label": "No source tag",
    "unknown_source": "Orders with no source tag",
    "subscribers": {"start": 1040, "per_unit": 0.3},          # Scene Library homes at the start, and the share of units that subscribe
    "pipeline": (236000, 372000),                             # business pipeline USD, the first and last week
    "installs": (2, 1, 3, 2, 4, 3, 5, 4),                     # business installs won, by week (repeats)
    "calibration": (84, 85, 84, 86, 85, 87, 86, 88),          # first-try calibration rate, %, by week (repeats)
    "returns": (3.6, 3.4, 3.1, 3.3, 2.9, 3.0, 2.8, 2.9),      # returns, %, by week (repeats)
    "usage": ("claude-opus-5", "anthropic", "subscription"),
    "tokens": (36000, 2600),                                  # input and output tokens of one weekly run
    "sources": {
        "store": {"truth_for": "Store orders", "system": "Acme online store", "reach_kind": "url",
                  "reach_ref": "https://shop.acme.example/admin/orders", "may": "Read orders and creator codes. No refunds.",
                  "owner_ref": "bot:email-marketing"},
        "ledger": {"truth_for": "Ad and creator spend", "system": "Finance ledger", "reach_kind": "person",
                   "reach_ref": "ana", "may": "Read spend by month. Ana approves changes.", "owner_ref": "bot:bookkeeper"},
        "marketplace": {"truth_for": "Marketplace units", "system": "bigmarket.example seller dashboard", "reach_kind": "url",
                        "reach_ref": "https://seller.bigmarket.example", "may": "Read orders and promoted-listing spend.",
                        "owner_ref": "bot:marketplace"},
        "retail": {"truth_for": "Retail sell-through", "system": "homeworks.example partner report", "reach_kind": "url",
                   "reach_ref": "https://partners.homeworks.example", "may": "Read the weekly sell-through report only.",
                   "owner_ref": "bot:retail"},
    },
    # Units per day at the start and end of the window, the cost per unit at each end, and the month's retention
    # (kept at 30, 90 and 180 days). `from_days` and `until_days` count back from the last run.
    "channels": [
        {"slug": "creator-videos", "name": "Creator videos", "track": "performance", "stage": "testing", "sort": 10,
         "owner": "bot:creators", "budget_month": 30000, "source": "store",
         "why": "Creators film a 30-second show on their own house. Their codes bring the buyer to the store.",
         "units": (19, 35), "cost": (30, 34), "bot_cost": 2.0, "revenue_month": 31, "margin_pct": 46, "kept": (85, 72, 60),
         "later": {"stage": "scaling", "budget_month": 45000, "before_end": 18,
                   "why": "Code orders held up for two weeks. Scaling to more creators for the holidays."}},
        {"slug": "online-store", "name": "Online store", "track": "performance", "stage": "scaling", "sort": 20,
         "owner": "bot:email-marketing", "budget_month": 12000, "source": "store",
         "why": "Search, email and direct visits to our own shop. The base that every season builds on.",
         "units": (26, 40), "cost": (28, 30), "bot_cost": 1.5, "revenue_month": 31, "margin_pct": 46, "kept": (86, 76, 66)},
        {"slug": "marketplace", "name": "Marketplace", "track": "performance", "stage": "scaling", "sort": 30,
         "owner": "bot:marketplace", "budget_month": 15000, "source": "marketplace",
         "why": "Listed on bigmarket.example. Its fees come out of the margin; promoted listings are the spend.",
         "units": (14, 26), "cost": (50, 54), "bot_cost": 3.0, "revenue_month": 27.5, "margin_pct": 36, "kept": (88, 78, 68)},
        {"slug": "retail-partners", "name": "Retail partners", "track": "brand", "stage": "testing", "sort": 40,
         "owner": "bot:retail", "budget_month": 9000, "source": "retail", "created_days": 35, "from_days": 35,
         "why": "Shelf space in homeworks.example stores. The partner reports sell-through each week.",
         "units": (7, 14), "cost": (42, 44), "bot_cost": 1.0, "revenue_month": 22, "margin_pct": 33, "kept": (90, 80, 70)},
        {"slug": "paid-ads", "name": "Paid ads", "track": "performance", "stage": "testing", "sort": 50,
         "owner": "bot:paid-media", "budget_month": 40000, "source": "store", "until_days": 11,
         "why": "Search and social ads for the Storefront. A test budget, then the holiday budget if it pays.",
         "units": (10, 11), "cost": (118, 146), "bot_cost": 5.0, "revenue_month": 31, "margin_pct": 44, "kept": (80, 60, 45),
         "later": {"stage": "paused", "budget_month": 0, "before_end": 11,
                   "why": "Cost per unit stayed above what a unit earns. Ads paused."}},
    ],
    "kpis": [
        {"key": "units", "name": "Units sold", "unit": "units", "owner": "marketing-lead", "role": "north_star",
         "stage": "acquire", "definition": "Units sold in the week, every channel together, no source tag included."},
        {"key": "subscribers", "name": "Scene Library subscribers", "unit": "homes", "owner": "marketing-lead",
         "role": "input", "stage": "activate", "parent": "units",
         "definition": "Homes on a Scene Library plan at the end of the week."},
        {"key": "pipeline", "name": "Business pipeline", "unit": "USD", "owner": "sales-lead", "role": "input",
         "stage": "reach", "parent": "units", "definition": "Open business quotes and site surveys, at the quoted price, at the end of the week."},
        {"key": "installs", "name": "Business installs won", "unit": "installs", "owner": "sales-lead", "role": "input",
         "stage": "acquire", "parent": "units", "definition": "Business installs signed in the week."},
        {"key": "calibration", "name": "First-try calibration rate", "unit": "%", "owner": "product-analyst",
         "role": "guardrail", "stage": "activate", "definition": "Share of set-ups whose phone calibration passed on the first try."},
        {"key": "returns", "name": "Returns", "unit": "%", "owner": "returns", "role": "guardrail", "stage": "retain",
         "direction": "down", "definition": "Share of the week's orders that came back."},
    ],
    # Marketing ideas: type Marketing, filed by Cara, moved by the bot that owns each. `days` count back from the last run.
    "ideas": [
        {"title": "Twelve creators film their own holiday show", "channel": "creator-videos", "owner": "bot:creators",
         "body": "Each creator films one 30-second clip on their own house, with the unit's code in the caption.",
         "created_days": 40,
         "steps": [{"step": "Live", "days": 38, "note": "Ten creators have their units. First clips go up on Monday."},
                   {"step": "Working", "days": 18, "note": "Code orders are steady. Adding creators for the holidays."},
                   {"step": "Paused", "days": 17, "note": "Holding at ten creators until the holiday list is confirmed."}]},
        {"title": "Holiday search and social ads for the Storefront", "channel": "paid-ads", "owner": "bot:paid-media",
         "body": "A small test aimed at homes with a big facade. It wins if a unit costs less than its margin.",
         "created_days": 40,
         "steps": [{"step": "Live", "days": 36, "note": "Test budget is live. Cost per unit checked each morning."},
                   {"step": "Failed", "days": 11, "close": True,
                    "note": "Cost per unit stayed above the margin. Ads paused, test closed."}]},
        {"title": "Starter bundle on bigmarket.example", "channel": "marketplace", "owner": "bot:marketplace",
         "body": "Home Starter with the mounting kit, listed for the marketplace only at a small discount.",
         "created_days": 14,
         "steps": [{"step": "Live", "days": 9, "note": "Bundle listed. Stock checked with inventory each Monday."},
                   {"step": "Paused", "days": 6, "note": "Holding the listing until inventory confirms the holiday stock."}]},
        {"title": "End-cap display at two homeworks.example stores", "channel": "retail-partners", "owner": "bot:retail",
         "body": "A display of Home Pro units at the end of two aisles. The partner sells them through on its own terms.",
         "created_days": 4,
         "steps": [{"step": "Paused", "days": 3, "note": "Waiting on homeworks.example to answer on the display terms."}]},
    ],
    "routine": {"bot": "marketing-lead", "key": "weekly-numbers", "title": "Weekly numbers", "cron": "0 7 * * 1",
                "timezone": "America/Los_Angeles",
                "text": "Each Monday: set this month's rows for every channel and Unknown with `hub growth cohort set`, "
                        "log last week's readings for the KPIs you own with `hub kpi log`, read `hub growth numbers` back, "
                        "and close this task with a short note."},
    "run": "Week of {week}: {units} units. {split}. Logged {readings} readings and set {rows} month rows.",
}


def moment(day, hour):
    """A Pacific day and hour, as the UTC moment the clock takes."""
    return datetime.combine(day, time(hour), ZONE).astimezone(timezone.utc)


def window(now):
    """The window's first Monday, and the Mondays whose previous week lies in it and whose 07:00 run has passed."""
    today = now.astimezone(ZONE).date()
    anchor = today - timedelta(weeks=CONTENT["weeks"])
    start = anchor - timedelta(days=anchor.weekday())
    runs, monday = [], start + timedelta(weeks=1)
    while monday <= today and moment(monday, 7) <= now:
        runs.append(monday)
        monday += timedelta(weeks=1)
    return start, runs


def model(start, runs):
    """One row per day, from the window's start to the last run: units, spend and bot cost by channel, and the units
    with no source tag. A channel's units ramp straight across the window, weighted by the day of the week."""
    total = (runs[-1] - start).days
    days = []
    for i in range(total):
        day = start + timedelta(days=i)
        f = i / max(1, total - 1)
        row = {"day": day, "units": {}, "spend": {}, "bot": {}}
        for ch in CONTENT["channels"]:
            first = total - ch["from_days"] if ch.get("from_days") else 0
            until = total - ch["until_days"] if ch.get("until_days") else total
            low, high = ch["units"]
            n = round((low + (high - low) * f) * CONTENT["weekday"][day.weekday()]) if first <= i < until else 0
            cost = ch["cost"][0] + (ch["cost"][1] - ch["cost"][0]) * f
            row["units"][ch["slug"]] = n
            row["spend"][ch["slug"]] = n * cost
            row["bot"][ch["slug"]] = n * ch["bot_cost"]
        row["units"][UNKNOWN] = round(CONTENT["unknown_share"] * sum(row["units"].values()))
        days.append(row)
    return days


def month_rows(days, cutoff):
    """Each month's sums by channel over the days before `cutoff`: what the routine sets on that Monday."""
    sums = {}
    for row in days:
        if row["day"] >= cutoff:
            break
        month = row["day"].strftime("%Y-%m")
        for slug, n in row["units"].items():
            if n:
                total = sums.setdefault((month, slug), {"first": row["day"], "units": 0, "spend": 0.0, "bot": 0.0})
                total["units"] += n
                total["spend"] += row["spend"].get(slug, 0)
                total["bot"] += row["bot"].get(slug, 0)
    return sums


def cohort(ch, month, total, monday):
    """One month row as the routine sets it. Retention lands once the month's first customers have reached it."""
    if ch is None:
        return {"channel": UNKNOWN, "month": month, "new_customers": total["units"], "quality": "estimate",
                "sources": {"new_customers": CONTENT["unknown_source"]}}
    sources = CONTENT["sources"]
    body = {"channel": ch["slug"], "month": month, "new_customers": total["units"], "quality": "actual",
            "spend": round(total["spend"], 2), "bot_cost": round(total["bot"], 2), "revenue_month": ch["revenue_month"],
            "margin_pct": ch["margin_pct"],
            "sources": {"spend": sources["ledger"]["truth_for"], "new_customers": sources[ch["source"]]["truth_for"]}}
    for after, key, value in zip((30, 90, 180), ("kept_d30", "kept_d90", "kept_d180"), ch["kept"], strict=True):
        if total["first"] + timedelta(days=after) <= monday:
            body[key] = value
    return body


def readings(days, k, runs):
    """Every KPI's reading for week `k` (the week before run `k`): a week's total, a stock at its end, or a value."""
    week = days[7 * k: 7 * k + 7]
    to_date = days[: 7 * k + 7]
    cumulative = sum(sum(row["units"].values()) for row in to_date)
    low, high = CONTENT["pipeline"]
    frac = k / max(1, runs - 1)

    def cycle(values):
        return values[k % len(values)]
    return {"units": sum(sum(row["units"].values()) for row in week),
            "subscribers": round(CONTENT["subscribers"]["start"] + CONTENT["subscribers"]["per_unit"] * cumulative),
            "pipeline": round((low + (high - low) * frac) / 1000) * 1000,
            "installs": cycle(CONTENT["installs"]),
            "calibration": cycle(CONTENT["calibration"]),
            "returns": cycle(CONTENT["returns"])}


def setup(b, s):
    """Settings, the sources the numbers come from, and the KPI tree, as Ana sets them."""
    b.call("PUT", "growth/settings", {"unit": CONTENT["unit"], **CONTENT["settings"]})
    for key, source in CONTENT["sources"].items():
        s["sources"][key] = b.call("POST", "growth/sources", source)["id"]
    for kpi in CONTENT["kpis"]:
        made = b.call("POST", "kpis", {"name": kpi["name"], "definition": kpi["definition"], "unit": kpi["unit"],
                                       "direction": kpi.get("direction", "up"), "cadence": "weekly", "owner": kpi["owner"]})
        s["kpis"][kpi["key"]] = made["kpi"]["id"]
    for kpi in CONTENT["kpis"]:
        fields = {"growth_role": kpi["role"], "stage": kpi["stage"]}
        if kpi.get("parent"):
            fields["parent_kpi_id"] = s["kpis"][kpi["parent"]]
        b.call("POST", "kpis/" + s["kpis"][kpi["key"]], fields)


def open_channel(b, s, ch):
    b.call("POST", "growth/channels", {"slug": ch["slug"], "name": ch["name"], "track": ch["track"], "stage": ch["stage"],
                                       "owner_ref": ch["owner"], "budget_month": ch["budget_month"],
                                       "source_id": s["sources"][ch["source"]], "why": ch["why"], "sort": ch["sort"]})


def later_stage(b, ch):
    """The owning bot moves its channel on: a new stage, budget and why."""
    fields = {k: v for k, v in ch["later"].items() if k != "before_end"}
    b.write(lambda c: growth.channel_update(c, ch["owner"], ch["slug"], fields))


def file_idea(b, s, idea):
    """Cara files the idea as a Marketing task on its channel."""
    made = b.call("POST", "tasks", {"title": idea["title"], "body": idea["body"], "owner": idea["owner"],
                                    "type": marketing_tasks.TYPE_ID, "labels": ["channel:" + idea["channel"]]},
                  token=TOKENS["human:cara"])
    s["ideas"][idea["title"]] = made["task"]["id"]


def move_idea(b, s, idea, step):
    """The owner moves the idea to its next step, or Cara closes it when the test is called."""
    tid = s["ideas"][idea["title"]]
    if step.get("close"):
        b.write(lambda c: hubdb.task_close(c, "human:cara", tid, note=step["note"], step=step["step"]))
    else:
        b.write(lambda c: hubdb.task_update(c, idea["owner"], tid, step=step["step"], note=step["note"]))


def weekly(b, s, days, runs, k, monday):
    """One Monday run of the routine: the month rows through the Sunday before, the week's readings, and the run's record."""
    routine = CONTENT["routine"]
    if k == 0:
        # Made after the cutover, so the routine is an automation at once: its runs are that automation's runs.
        made = b.write(lambda c: routines.create(
            c, "human:ana", routine["bot"], {"title": routine["title"], "cron": routine["cron"], "text": routine["text"],
                                             "timezone": routine["timezone"]}, key=routine["key"], at=b.clock.at))
        s["routine"], s["automation"] = made["id"], made["automation"]
    week = days[7 * k: 7 * k + 7]
    first, last = monday - timedelta(days=7), monday - timedelta(days=1)
    sums = month_rows(days, monday)
    values = readings(days, k, len(runs))
    by_slug = {ch["slug"]: ch for ch in CONTENT["channels"]}
    split = [(ch["name"], sum(row["units"].get(slug, 0) for row in week)) for slug, ch in by_slug.items()]
    split.append((CONTENT["unknown_label"], sum(row["units"][UNKNOWN] for row in week)))
    summary = CONTENT["run"].format(
        week=f"{first:%b} {first.day} to {last:%b} {last.day}",
        units=sum(sum(row["units"].values()) for row in week),
        split=", ".join(f"{name} {n}" for name, n in split), readings=len(CONTENT["kpis"]), rows=len(sums))

    due = moment(monday, 7)

    def fire(c):
        # The schedule's fire, as the scheduler's tick makes it: the engine starts the run, and its bot turn opens the
        # routine's task and wakes the bot on it.
        return automations.fire(c, "schedule", {"schedule": {"at": stamp(due), "cron": routine["cron"],
                                                             "tz": routine["timezone"]}},
                                f"schedule:{stamp(due)}", only=s["automation"])
    queued = b.write(fire)
    if len(queued) != 1:
        raise RuntimeError(f"the routine's run for {monday} was not queued: {queued}")
    b.store.automations.drain()
    run_id = queued[0]

    def work(c):
        run = c.execute("SELECT * FROM automation_runs WHERE id=?", (run_id,)).fetchone()
        ref = next(step["outputs"]["ref"] for step in json.loads(run["saved_state"])["steps"] if step["type"] == "bot_turn")
        # The wake the turn runs from: its message names the run, and its job is the turn's work.
        job = c.execute("SELECT j.id AS job_id, j.message_id FROM jobs j JOIN messages m ON m.id=j.message_id "
                        "WHERE json_extract(m.refs,'$.automation_run')=?", (run_id,)).fetchone()
        for (month, slug), total in sums.items():
            growth.cohort_set(c, "bot:" + routine["bot"], models.GrowthCohortSet(**cohort(by_slug.get(slug), month, total, monday)))
        for kpi in CONTENT["kpis"]:
            goals.kpi_log(c, "bot:" + kpi["owner"], s["kpis"][kpi["key"]], values[kpi["key"]],
                          period_start=(monday - timedelta(days=7)).isoformat(), period_end=(monday - timedelta(days=1)).isoformat(),
                          quality="measured")
        hubdb.say(c, "bot:" + routine["bot"], "human:ana", summary, refs={"task": ref})
        turn = hubdb.turn_start(c, hubdb.KEEPER, routine["bot"], trigger="routine", message_id=job["message_id"], task_id=ref)
        b.clock.at += timedelta(minutes=4)
        model_name, provider, billing = CONTENT["usage"]
        tokens_in, tokens_out = CONTENT["tokens"]
        cached = tokens_in * 2 // 3
        cost = providers.estimate_cost(model_name, tokens_in - cached, cached, tokens_out)
        hubdb.turn_finish(c, hubdb.KEEPER, turn["id"], "completed", summary,
                          usage={"input_tokens": tokens_in - cached, "cached_tokens": cached, "output_tokens": tokens_out,
                                 "model": model_name, "provider": provider, "est_cost_usd": cost, "billing": billing})
        hubdb.task_update(c, "bot:" + routine["bot"], ref, status="done", note=summary)
        c.execute("UPDATE jobs SET state='completed' WHERE id=?", (job["job_id"],))
        # The turn's end, as the runner reports it: the run takes the turn's answer and cost, and its day counts.
        automations.turn_done(c, {"id": turn["id"], "job_id": job["job_id"]}, "completed", summary)
        hubdb.status_result(c, hubdb.KEEPER, routine["bot"], last_result=summary, last_turn_at=hubdb.now())
    b.write(work)


def schedule(b, s):
    """After the history: the automation's next run is the next Monday 07:00 PT after now. A moved routine's own update
    keeps its cursor, so the cursor is set the way the scheduler sets it after each fire."""
    def work(c):
        row = c.execute("SELECT * FROM automations WHERE id=?", (s["automation"],)).fetchone()
        trigger = automations.flow_of(row)["trigger"]
        c.execute("UPDATE automations SET next_due_at=? WHERE id=?",
                  (stamp(next_due(trigger["cron"], b.now, trigger.get("tz") or "UTC")), s["automation"]))
    b.write(work)


def seed(b):
    start, runs = window(b.now)
    days, last = model(start, runs), runs[-1]
    s = {"sources": {}, "kpis": {}, "ideas": {}, "routine": None, "automation": None}
    steps = [(moment(start, 9), lambda: setup(b, s))]
    for ch in CONTENT["channels"]:
        opened = last - timedelta(days=ch["created_days"]) if ch.get("created_days") else start
        steps.append((moment(opened, 9), lambda ch=ch: open_channel(b, s, ch)))
        if ch.get("later"):
            steps.append((moment(last - timedelta(days=ch["later"]["before_end"]), 9), lambda ch=ch: later_stage(b, ch)))
    for idea in CONTENT["ideas"]:
        steps.append((moment(last - timedelta(days=idea["created_days"]), 10), lambda idea=idea: file_idea(b, s, idea)))
        for step in idea["steps"]:
            steps.append((moment(last - timedelta(days=step["days"]), 10), lambda idea=idea, step=step: move_idea(b, s, idea, step)))
    for k, monday in enumerate(runs):
        steps.append((moment(monday, 7), lambda k=k, monday=monday: weekly(b, s, days, runs, k, monday)))
    steps.append((b.now, lambda: schedule(b, s)))
    for when, work in sorted(steps, key=lambda step: step[0]):
        b.clock.at = when
        work()
