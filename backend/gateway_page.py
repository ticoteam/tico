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
    return {
        "id": found["id"],
        "name": found["label"],
        "model": found["model"],
        "version": "auto" if found["auto"] else found["version"],
        "bots": bots,
        "price": gateway_tiers.money(found["price"]),
    }


def picks(c, settings=None):
    """The Gateway picks the active bots use, most used first."""
    counts = {}
    for row in c.execute(
        f"SELECT b.slug, {BS.CONFIG} AS config FROM bots b JOIN bot_config bc ON bc.bot=b.id {BS.JOIN} "
        "WHERE b.state<>'archived'"
    ):
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
    merged = {}
    for model, bots in counts.items():
        found = gateway_tiers.resolve(model, cat)
        if found and found["kind"] == "chat":
            # An id from before this lineup counts under the id it is now.
            merged[found["id"]] = (found, merged.get(found["id"], (found, 0))[1] + bots)
    out = [pick_row(found, bots) for found, bots in merged.values()]
    return sorted(out, key=lambda r: (-r["bots"], r["name"]))


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
    return {
        "model": now["model"],
        "price": _price(now["price"]),
        "long": f"{_price(long)} past {_tokens(long['from'])}" if long else "",
        "context": now.get("context") or 0,
        "inputs": list(now.get("inputs") or ["text"]),
    }


def models(cat):
    """The models table of the Gateway card: one row per level with its model and its Fast model. A tier that runs a
    held model is not shown (`gateway_tiers.live_row`)."""
    live = [row for row in map(gateway_tiers.live_row, cat["models"]) if row is not None]
    tiers = {(row["level"], bool(row["fast"])): _now(row) for row in live if row.get("level")}
    meeting = next((row for row in live if row["id"] == gateway_tiers.MEETING_TIER), None)
    return {
        "levels": [
            {"id": level, "name": name, "model": tiers[(level, False)], "fast": tiers.get((level, True))}
            for level, name in ((row["id"], row["name"]) for row in cat["levels"])
            if (level, False) in tiers
        ],
        "meeting": {"name": meeting["name"], **_now(meeting)} if meeting else None,
        "decisions": [
            {"id": row["id"], "name": row["name"], **_now(row)}
            for row in map(gateway_tiers.live_row, cat["decisions"])
            if row is not None and row["versions"]
        ],
    }


def overview(c, settings=None, *, owner=False):
    """The Tico Gateway card on Settings > Models: the connection with this month's spend against its cap, the picks in use, the models
    and the services."""
    cat = catalog(c, settings)
    rows = picks(c, settings)
    return {
        "connection": gateway_connection.view(c, settings, manage=owner),
        "as_of": cat.get("as_of", ""),
        "picks": rows,
        "models": models(cat),
        "services": [
            {**row, "for": row.get("for") or "", "tools": row["tools"], "details": row.get("details") or []}
            for row in cat["services"]
        ],
    }
