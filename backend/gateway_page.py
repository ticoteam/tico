"""What the Tico Gateway card on Settings > Models shows and what an agent reads about the Tico AI Gateway's lineup.

The catalog is backend/gateway_tiers.py's (HQ's, refreshed while the Gateway is connected, else this release's). The
picks in use are counted from the bots' own settings: a bot counts once for each pick it uses, as its model, its
fallback or its task model."""
import json

from . import gateway_connection, gateway_tiers, providers
from . import bot_settings as BS


def catalog(c, settings=None):
    """The catalog in use, asking HQ for a fresh one in the background while the Gateway is connected. A rehearsal (a
    copy of real data) and a demo never ask HQ: they show the catalog already in use, or this release's copy."""
    quiet = getattr(settings, "rehearsal", False) or getattr(settings, "demo", False)
    base = gateway_connection.url(c, settings) if gateway_connection.connected(c, settings) and not quiet else None
    return gateway_tiers.refresh_soon(base)


def pick_row(found, bots):
    """One row of the picks table from `gateway_tiers.resolve`: no "Tico" in the name, the version or `auto`."""
    old = found["old"]
    return {"id": found["id"], "name": found["label"], "model": found["model"],
            "version": "auto" if found["auto"] else found["version"] + (" (old lineup)" if old else ""),
            "bots": bots, "price": gateway_tiers.money(found["price"]), "old": old}


def picks(c, settings=None):
    """The Gateway picks the active bots use, most used first."""
    record = providers.load(c, settings)
    counts = {}
    for row in c.execute(f"SELECT b.slug, {BS.CONFIG} AS config FROM bots b JOIN bot_config bc ON bc.bot=b.id {BS.JOIN} "
                         "WHERE b.state<>'archived'"):
        try:
            config = json.loads(row["config"] or "{}")
        except ValueError:
            continue
        used = {providers.bot_choice(c, settings, config)[1]}
        for key in ("fallback", "task_model"):
            if isinstance(config.get(key), dict) and config[key].get("model"):
                used.add(str(config[key]["model"]))
        for model in used:
            counts[model] = counts.get(model, 0) + 1
    cat = gateway_tiers.catalog()
    out = []
    for model, bots in counts.items():
        found = gateway_tiers.resolve(model, cat)
        if found and found["kind"] == "chat":
            out.append(pick_row(found, bots))
    return sorted(out, key=lambda r: (-r["bots"], r["name"])), record["decide_level"]


def _tokens(n):
    """`1M`, `1.05M` or `100K`, like the UI's modelContextWords."""
    return f"{round(n / 1_000_000, 2):g}M" if n >= 1_000_000 else f"{round(n / 1000)}K"


def _price(price):
    """`$0.15 / $0.75 per 1M`, or `$0.06 per 1M input` for a model whose output is free (Decide Low)."""
    if not price:
        return ""
    return gateway_tiers.money(price) if price.get("output") else f"${price['input']:.2f} per 1M input"


def _now(tier):
    """What a tier or a decision runs at its current version: the model, its price (and its long-prompt price), context
    and inputs. A version that names no inputs reads text only, as `gateway_tiers.resolve` reads it."""
    now = next((v for v in tier["versions"] if v["version"] == tier["current"]), tier["versions"][-1])
    long = now.get("long_prompt")
    return {"model": now["model"], "price": _price(now["price"]),
            "long": f"{_price(long)} past {_tokens(long['from'])}" if long else "",
            "context": now.get("context") or 0, "inputs": list(now.get("inputs") or ["text"])}


def models(cat):
    """The models table of the Gateway card: one row per level with its model and its Fast model. Every department
    runs a level on the same model today; if one ever differs, the rows are per department instead (`same` false).
    A tier that runs a held model is not shown (`gateway_tiers.live_row`)."""
    names = {row["id"]: row["name"] for row in cat["departments"]}
    live = [row for row in map(gateway_tiers.live_row, cat["models"]) if row is not None]
    tiers = {(row["department"], row["level"], bool(row["fast"])): _now(row) for row in live if row.get("department")}
    departments = [ident for ident in names if any(key[0] == ident for key in tiers)]
    levels = [(row["id"], row["name"]) for row in cat["levels"]]
    same = bool(departments) and all(tiers.get((d, level, fast)) == tiers.get((departments[0], level, fast))
                                     for d in departments for level, _ in levels for fast in (False, True))
    groups = [("", departments[0])] if same else [(names[d] + " · ", d) for d in departments]
    rows = [{"id": level if same else f"{department}-{level}", "name": prefix + name,
             "model": tiers[(department, level, False)], "fast": tiers.get((department, level, True))}
            for prefix, department in groups for level, name in levels if (department, level, False) in tiers]
    meeting = next((row for row in live if row["id"] == gateway_tiers.MEETING_TIER), None)
    return {"same": same, "departments": [names[d] for d in departments], "levels": rows,
            "meeting": {"name": meeting["name"], **_now(meeting)} if meeting else None,
            "decisions": [{"id": row["id"], "name": row["name"], **_now(row)}
                          for row in map(gateway_tiers.live_row, cat["decisions"]) if row is not None and row["versions"]]}


def overview(c, settings=None, *, owner=False):
    """The Tico Gateway card on Settings > Models: the connection with this month's spend against its cap, the picks in use, the models
    and the services."""
    cat = catalog(c, settings)
    rows, decide = picks(c, settings)
    return {"connection": gateway_connection.view(c, settings, manage=owner), "as_of": cat.get("as_of", ""),
            "picks": rows, "decide_level": decide, "models": models(cat),
            "services": [{**row, "for": row.get("for") or "", "tools": row["tools"], "details": row.get("details") or []}
                         for row in cat["services"]]}
