"""What Settings > Gateway shows and what an agent reads about the Tico AI Gateway's lineup.

The catalog is backend/gateway_tiers.py's (HQ's, refreshed while the Gateway is connected, else this release's). The
picks in use are counted from the bots' own settings: a bot counts once for each pick it uses, as its model, its
fallback or its task model."""
import json

from . import gateway_connection, gateway_tiers, providers


def catalog(c, settings=None):
    """The catalog in use, asking HQ for a fresh one in the background while the Gateway is connected."""
    rehearsal = getattr(settings, "rehearsal", False)      # a copy of real data asks HQ for nothing
    base = gateway_connection.url(c, settings) if gateway_connection.connected(c, settings) and not rehearsal else None
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
    for row in c.execute("SELECT b.slug, bc.config FROM bots b JOIN bot_config bc ON bc.bot=b.slug "
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


def overview(c, settings=None, *, owner=False):
    """The Settings > Gateway page: the connection with this month's spend against its cap, the picks in use and the
    services."""
    cat = catalog(c, settings)
    rows, decide = picks(c, settings)
    return {"connection": gateway_connection.view(c, settings, manage=owner), "as_of": cat.get("as_of", ""),
            "picks": rows, "decide_level": decide,
            "services": [{**row, "tools": row["tools"]} for row in cat["services"]]}
