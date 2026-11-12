"""The gate's report: per level, per route, what the sender saw beside what the server recorded, the write lock, the
server's CPU and memory, live delivery delay, and the sender's own lateness and CPU, with "valid" or "not valid".

Only the steady window counts: requests planned inside it, flight recorder minutes inside it. A level whose sender went
out late (p95 lateness over the limit) or ran out of CPU is not valid, and its numbers must not be used: the server
was not the limit. Each number is checked against a budget file when one is given (`budget.json` lines, see README)."""

import json
import math

LATENESS_P95_MS = 50.0  # the sender's p95 lateness above this makes a level not valid
LATENESS_MAX_MS = 1000.0  # and any single send this late
SENDER_CPU_SHARE = 0.9  # a sender process busier than this share of one CPU is the limit
MIN_REQUESTS = 1000  # repeatability (Measure 3) compares routes with at least this many requests


def pct(values, q):
    """Nearest-rank percentile; None for no values."""
    if not values:
        return None
    values = sorted(values)
    return round(values[min(len(values) - 1, max(0, math.ceil(q * len(values)) - 1))], 2)


def sender_side(logs, since, until):
    """Per route from the sender's logs, counting requests planned in [since, until)."""
    routes, lateness = {}, []
    for log in logs:
        for planned, route, ms, status, size, late in log["requests"]:
            if not since <= planned < until:
                continue
            r = routes.setdefault(route, {"ms": [], "errors": 0, "rejected": 0, "bytes": 0})
            r["ms"].append(ms)
            r["bytes"] += size
            if status >= 500 or status == 0:
                r["errors"] += 1
            elif status >= 400:
                r["rejected"] += 1
            lateness.append(late)
    out = {}
    for route, r in sorted(routes.items()):
        out[route] = {
            "requests": len(r["ms"]),
            "errors": r["errors"],
            "rejected_4xx": r["rejected"],
            "bytes": r["bytes"],
            "sender_p50": pct(r["ms"], 0.5),
            "sender_p95": pct(r["ms"], 0.95),
            "sender_p99": pct(r["ms"], 0.99),
        }
    return out, lateness


def level_report(level, logs, flight, since, until, budget=None):
    """One level's section. `flight` is the server's flight recorder view of the window (flight.view), or None."""
    routes, lateness = sender_side(logs, since, until)
    server_routes = {r["route"]: r for r in ((flight or {}).get("requests") or {}).get("routes", [])}
    for route, row in routes.items():
        s = server_routes.get(route)
        row["server_requests"] = s["n"] if s else None
        row["server_p50"] = s["p50"] if s else None
        row["server_p95"] = s["p95"] if s else None
    seconds = max(1e-9, until - since)
    lag = [ms for log in logs for at, ms in log["lag"] if since <= at < until]
    deliveries = [ms for log in logs for at, ms in log["deliveries"] if since <= at < until]
    cpu_shares = [log["cpu_s"] / max(1e-9, log["wall_s"]) for log in logs]
    process = (flight or {}).get("process") or {}
    sender = {
        "processes": len(logs),
        "lateness_p95_ms": pct(lateness, 0.95),
        "lateness_max_ms": round(max(lateness), 1) if lateness else None,
        "loop_lag_p95_ms": pct(lag, 0.95),
        "cpu_share_max": round(max(cpu_shares), 3) if cpu_shares else None,
        "cpu_share_mean": round(sum(cpu_shares) / len(cpu_shares), 3) if cpu_shares else None,
        "turns_completed": sum(log["turns"] for log in logs),
        # The level is the slots; this is how many turns really ran at once, on average over the window.
        "turns_running_mean": round(
            sum(max(0.0, min(b, until) - max(a, since)) for log in logs for a, b in log.get("spans", [])) / seconds, 1
        ),
        "claims_empty": sum(log["claims_empty"] for log in logs),
    }
    why = []
    if sender["lateness_p95_ms"] is None:
        why.append("no requests in the steady window")
    elif sender["lateness_p95_ms"] > LATENESS_P95_MS:
        why.append(f"sender p95 lateness {sender['lateness_p95_ms']} ms over {LATENESS_P95_MS} ms")
    if sender["lateness_max_ms"] is not None and sender["lateness_max_ms"] > LATENESS_MAX_MS:
        why.append(f"a send {sender['lateness_max_ms']} ms late, over {LATENESS_MAX_MS} ms")
    if sender["cpu_share_max"] is not None and sender["cpu_share_max"] > SENDER_CPU_SHARE:
        why.append(f"a sender process used {sender['cpu_share_max']:.0%} of a CPU")
    if flight is None:
        why.append("no flight recorder window read from the server")
    out = {
        "level": level,
        "window": {"since": since, "until": until, "seconds": round(seconds, 1)},
        "valid": not why,
        "not_valid_because": why,
        "requests": sum(r["requests"] for r in routes.values()),
        "requests_per_second": round(sum(r["requests"] for r in routes.values()) / seconds, 1),
        "routes": routes,
        "server": {
            "flight_window": {"since": (flight or {}).get("since"), "until": (flight or {}).get("until")},
            "requests": ((flight or {}).get("requests") or {}).get("n"),
            "p50": ((flight or {}).get("requests") or {}).get("p50"),
            "p95": ((flight or {}).get("requests") or {}).get("p95"),
            "cpu_avg": process.get("cpu_avg"),
            "cpu_max": process.get("cpu_max"),
            "rss": process.get("rss"),
            "lock_wait_max_ms": process.get("wait_max"),
            "lock_hold_max_ms": process.get("hold_max"),
            "lock_hold_p95_ms": process.get("hold_p95"),
            "loop_lag_max_ms": process.get("lag_max"),
        },
        "live_delivery": {
            "deliveries": len(deliveries),
            "p50_ms": pct(deliveries, 0.5),
            "p95_ms": pct(deliveries, 0.95),
            "max_ms": round(max(deliveries), 1) if deliveries else None,
        },
        "sender": sender,
    }
    out["budget"] = check_budget(out, budget) if budget else []
    return out


def metric(level, line):
    """The value a budget line names, from a level's section."""
    m, route = line["metric"], line.get("route")
    if m == "route_server_p95_ms":
        return (level["routes"].get(route) or {}).get("server_p95")
    if m == "route_sender_p95_ms":
        return (level["routes"].get(route) or {}).get("sender_p95")
    if m == "server_p95_ms":
        return level["server"]["p95"]
    if m == "lock_wait_max_ms":
        return level["server"]["lock_wait_max_ms"]
    if m == "lock_hold_p95_ms":
        return level["server"]["lock_hold_p95_ms"]
    if m == "server_cpu_avg":
        return level["server"]["cpu_avg"]
    if m == "live_delivery_p95_ms":
        return level["live_delivery"]["p95_ms"]
    if m == "error_share":
        return round(sum(r["errors"] for r in level["routes"].values()) / max(1, level["requests"]), 5)
    raise ValueError(f"unknown budget metric {m}")


def check_budget(level, budget):
    out = []
    for line in budget.get("lines", []):
        value = metric(level, line)
        out.append(
            {
                "line": line.get("id") or line["metric"],
                "source": line.get("source", ""),
                "value": value,
                "max": line["max"],
                "result": "no data" if value is None else ("met" if value <= line["max"] else "missed"),
            }
        )
    return out


def compare(a, b, share=0.15, minimum=MIN_REQUESTS):
    """Measures 3 and 4: per route present in both reports' level with at least `minimum` requests, the server p95 of
    each and whether they are within `share` of each other."""
    out = []
    levels_b = {lv["level"]: lv for lv in b["levels"]}
    for lv in a["levels"]:
        other = levels_b.get(lv["level"])
        if not other:
            continue
        for route, row in lv["routes"].items():
            o = other["routes"].get(route)
            if not o or min(row["requests"], o["requests"]) < minimum:
                continue
            for side in ("server_p95", "sender_p95"):
                x, y = row.get(side), o.get(side)
                if x is None or y is None:
                    continue
                spread = abs(x - y) / max(1e-9, min(x, y))
                out.append(
                    {
                        "level": lv["level"],
                        "route": route,
                        "side": side,
                        "a": x,
                        "b": y,
                        "delta_ms": round(y - x, 2),
                        "within": spread <= share,
                    }
                )
    return out


def markdown(report):
    """The short table a person reads first; the JSON beside it has everything."""
    head = report["run"]
    lines = [
        f"# Load gate report: {head['commit'][:12] or 'unknown commit'}",
        "",
        f"Seed {head['seed']}, server {head['server_host']}, sender {head['sender_host']}, started {head['started']}.",
        f"Data: {', '.join(f'{k} {v:,}' for k, v in head['rows'].items())}.",
        f"Server commit (its flight recorder): {head.get('server_commit') or 'unknown'}; pinned: {head['pinned']}.",
        "",
    ]
    for lv in report["levels"]:
        s, srv, d = lv["sender"], lv["server"], lv["live_delivery"]
        verdict = "valid" if lv["valid"] else "NOT VALID: " + "; ".join(lv["not_valid_because"])
        lines += [
            f"## Level {lv['level']} turns at once: {verdict}",
            "",
            f"{lv['requests']:,} requests in {lv['window']['seconds']:.0f} s steady ({lv['requests_per_second']}/s), "
            f"{s['turns_running_mean']} turns running on average. "
            f"Sender lateness p95 {s['lateness_p95_ms']} ms, max {s['lateness_max_ms']} ms; sender CPU max "
            f"{s['cpu_share_max']}. Server CPU avg {srv['cpu_avg']}, max {srv['cpu_max']}; RSS {srv['rss']}. "
            f"Write lock wait max {srv['lock_wait_max_ms']} ms, hold max {srv['lock_hold_max_ms']} ms, hold p95 "
            f"{srv['lock_hold_p95_ms']} ms. Live delivery p95 {d['p95_ms']} ms over {d['deliveries']} deliveries.",
            "",
            "| Route | Requests | 5xx | 4xx | Sender p50 | p95 | p99 | Server p50 | p95 |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for route, r in sorted(lv["routes"].items(), key=lambda kv: -kv[1]["requests"]):
            lines.append(
                f"| {route} | {r['requests']:,} | {r['errors']} | {r['rejected_4xx']} | {r['sender_p50']} | "
                f"{r['sender_p95']} | {r['sender_p99']} | {r['server_p50']} | {r['server_p95']} |"
            )
        if lv["budget"]:
            lines += ["", "| Budget line | Value | Max | Result |", "|---|---:|---:|---|"]
            lines += [f"| {b['line']} | {b['value']} | {b['max']} | {b['result']} |" for b in lv["budget"]]
        lines.append("")
    return "\n".join(lines)


def dump(report, folder, name="report"):
    from pathlib import Path

    folder = Path(folder)
    (folder / f"{name}.json").write_text(json.dumps(report, indent=1))
    (folder / f"{name}.md").write_text(markdown(report))
    return folder / f"{name}.json", folder / f"{name}.md"
