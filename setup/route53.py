"""Create the records in Route 53, but only in a hosted zone the internet actually asks."""

from __future__ import annotations

from .aws import DEFAULT_NAMES, Names
from .dns import Record, Zone


def find_zone(client, zone: Zone) -> tuple[str, str]:
    """Returns (zone id, status). A hosted zone can exist without being the delegated one: two pilot installs hit
    that (Route 53 held a zone while Netlify/NS1 served the domain), so a match on name alone is not enough."""
    r = client.list_hosted_zones_by_name(DNSName=zone.name, MaxItems="5")["HostedZones"]
    cands = [z for z in r if z["Name"].rstrip(".") == zone.name and not z.get("Config", {}).get("PrivateZone")]
    if not cands:
        return "", "missing"
    for z in cands:
        zid = z["Id"].split("/")[-1]
        ns = {n.rstrip(".").lower() for n in client.get_hosted_zone(Id=zid)["DelegationSet"]["NameServers"]}
        if ns & set(zone.nameservers):
            return zid, "authoritative"
    return cands[0]["Id"].split("/")[-1], "not-authoritative"


def upsert(client, zone_id: str, records: list[Record], names: Names = DEFAULT_NAMES) -> None:
    changes = [
        {
            "Action": "UPSERT",
            "ResourceRecordSet": {
                "Name": r.name,
                "Type": r.type,
                "TTL": r.ttl,
                "ResourceRecords": [{"Value": r.value}],
            },
        }
        for r in records
    ]
    client.change_resource_record_sets(
        HostedZoneId=zone_id, ChangeBatch={"Comment": names.dns_comment, "Changes": changes}
    )


def _norm(v: str) -> str:
    return v.rstrip(".").lower()


def plan(
    client,
    zone_id: str,
    records: list[Record],
    replaceable: set[str],
    allowed: set[str] = frozenset(),
    *,
    preflight=False,
) -> tuple[list[dict], set[str], list[dict]]:
    """(changes, the names that get a CREATE, conflicts) for writing `records`, from the record sets read now.

    A record set that already holds exactly a wanted record is left alone (its TTL too) and does not become setup's. One
    at a name in `replaceable` (names this install made, or the requested one under --replace-dns) is deleted exactly as
    read, in the same batch as the CREATE: Route 53 refuses the whole batch if it changed since, and one made meanwhile
    makes the CREATE fail. Anything else at the name is someone else's: a conflict. A CNAME shares its name with nothing;
    an A or AAAA holding only `allowed` (this server's addresses) may stay beside the wanted record. preflight: the
    wanted values may be placeholders, so a record holding only `allowed` addresses is not a conflict yet."""
    changes, created, conflicts = [], set(), []
    for want in sorted({_norm(r.name) for r in records}):
        mine = [r for r in records if _norm(r.name) == want]
        wanted: dict[str, set[str]] = {}
        for r in mine:
            wanted.setdefault(r.type, set()).add(_norm(r.value))
        cname = "CNAME" in wanted
        got = client.list_resource_record_sets(
            HostedZoneId=zone_id, StartRecordName=want, StartRecordType="A", MaxItems="20"
        )
        keep = set()
        for rs in got["ResourceRecordSets"]:
            if _norm(rs["Name"]) != want or rs["Type"] not in ("A", "AAAA", "CNAME"):
                continue
            alias, w = "AliasTarget" in rs, wanted.get(rs["Type"])
            vals = {_norm(v["Value"]) for v in rs.get("ResourceRecords", [])}
            if not alias and w is not None and vals == w:
                keep.add(rs["Type"])
                continue
            if want in replaceable:
                changes.append({"Action": "DELETE", "ResourceRecordSet": rs})
                continue
            fits = not alias and rs["Type"] != "CNAME" and not cname and vals
            mine_only = {_norm(a) for a in allowed}
            if fits and (vals <= mine_only if w is None else preflight and vals <= mine_only | w):
                continue
            conflicts.append(rs)
        for t in sorted(set(wanted) - keep):
            first = next(r for r in mine if r.type == t)
            values = sorted({r.value for r in mine if r.type == t})
            rrset = {"Name": first.name, "Type": t, "TTL": first.ttl, "ResourceRecords": [{"Value": v} for v in values]}
            changes.append({"Action": "CREATE", "ResourceRecordSet": rrset})
            created.add(want)
    return changes, created, conflicts


def apply(client, zone_id: str, changes: list[dict], names: Names = DEFAULT_NAMES) -> None:
    """One batch, deletes first: all of it happens or none of it does."""
    if changes:
        ordered = sorted(changes, key=lambda c: c["Action"] != "DELETE")
        client.change_resource_record_sets(
            HostedZoneId=zone_id, ChangeBatch={"Comment": names.dns_comment, "Changes": ordered}
        )


def describe(rs: dict) -> str:
    target = (
        rs["AliasTarget"]["DNSName"] if "AliasTarget" in rs else ", ".join(v["Value"] for v in rs["ResourceRecords"])
    )
    return f"{rs['Type']} {rs['Name'].rstrip('.')} -> {target}"


def pointing_at(client, zone_id: str, name: str, addresses: set[str]) -> tuple[list[dict], list[dict]]:
    """The A and AAAA record sets for `name`, split into (ours, not ours). Ours means every value is one of `addresses`; an
    alias, or a record someone has since pointed elsewhere, is never ours."""
    want = name.rstrip(".").lower()
    r = client.list_resource_record_sets(HostedZoneId=zone_id, StartRecordName=want, StartRecordType="A", MaxItems="10")
    ours, other = [], []
    for rs in r["ResourceRecordSets"]:
        if rs["Name"].rstrip(".").lower() != want or rs["Type"] not in ("A", "AAAA"):
            continue
        values = {v["Value"] for v in rs.get("ResourceRecords", [])}
        (ours if values and "AliasTarget" not in rs and values <= addresses else other).append(rs)
    return ours, other


def delete(client, zone_id: str, record_sets: list[dict], names: Names = DEFAULT_NAMES) -> None:
    """Deletes exactly these record sets: Route 53 refuses the batch if any has changed since it was read."""
    changes = [{"Action": "DELETE", "ResourceRecordSet": rs} for rs in record_sets]
    client.change_resource_record_sets(
        HostedZoneId=zone_id, ChangeBatch={"Comment": f"{names.dns_comment} destroy", "Changes": changes}
    )
