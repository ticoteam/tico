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


# What, besides name, type, TTL and values, makes a record set what it is: its routing policy and health check.
ROUTING = (
    "SetIdentifier",
    "Weight",
    "Region",
    "Failover",
    "GeoLocation",
    "GeoProximityLocation",
    "CidrRoutingConfig",
    "MultiValueAnswer",
    "HealthCheckId",
    "TrafficPolicyInstanceId",
)


def _canon(v):
    return tuple(sorted((k, _canon(x)) for k, x in v.items())) if isinstance(v, dict) else v


def simple(rs: dict) -> bool:
    """A plain record set, as setup writes them: no alias, no routing policy, no health check."""
    return "AliasTarget" not in rs and not any(k in rs for k in ROUTING)


def key(rs: dict) -> tuple | None:
    """A record set exactly: name, type, TTL, values and every routing field. An alias is never one setup made."""
    if "AliasTarget" in rs:
        return None
    values = tuple(sorted(_norm(v["Value"]) for v in rs.get("ResourceRecords", [])))
    return (_norm(rs["Name"]), rs["Type"], rs.get("TTL"), values, tuple((k, _canon(rs.get(k))) for k in ROUTING))


def plan(
    client,
    zone_id: str,
    records: list[Record],
    owned: list[dict],
    replace: set[str] = frozenset(),
    allowed: set[str] = frozenset(),
    *,
    preflight=False,
) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    """(changes, the record sets created, conflicts, the owned record sets kept) for writing `records`, from the record
    sets read now.

    A record set exactly equal to one in `owned` (what this install recorded creating, or journalled before a CREATE whose
    answer was lost) is this install's: kept when it already holds the wanted values, else deleted exactly as read in the
    same batch as the CREATE (Route 53 refuses the whole batch if it changed since, and one made meanwhile makes the
    CREATE fail). So is anything at a name in `replace` (--replace-dns). Any other record set holding exactly a wanted
    record is left alone and does not become this install's; anything else at the name is someone else's: a conflict. A
    CNAME shares its name with nothing; an A or AAAA holding only `allowed` (this server's addresses) may stay beside
    the wanted record. preflight: the wanted values may be placeholders, so one holding only `allowed` is not a conflict
    yet."""
    mine_keys = {key(rs) for rs in owned} - {None}
    changes, created, conflicts, kept = [], [], [], []
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
            w = wanted.get(rs["Type"])
            vals = {_norm(v["Value"]) for v in rs.get("ResourceRecords", [])}
            if simple(rs) and w is not None and vals == w:
                keep.add(rs["Type"])
                if key(rs) in mine_keys:
                    kept.append(rs)
                continue
            if key(rs) in mine_keys or want in replace:
                changes.append({"Action": "DELETE", "ResourceRecordSet": rs})
                continue
            fits = simple(rs) and rs["Type"] != "CNAME" and not cname and vals
            mine_only = {_norm(a) for a in allowed}
            if fits and (vals <= mine_only if w is None else preflight and vals <= mine_only | w):
                continue
            conflicts.append(rs)
        for t in sorted(set(wanted) - keep):
            first = next(r for r in mine if r.type == t)
            values = sorted({r.value for r in mine if r.type == t})
            rrset = {"Name": first.name, "Type": t, "TTL": first.ttl, "ResourceRecords": [{"Value": v} for v in values]}
            changes.append({"Action": "CREATE", "ResourceRecordSet": rrset})
            created.append(rrset)
    return changes, created, conflicts, kept


def apply(client, zone_id: str, changes: list[dict], names: Names = DEFAULT_NAMES) -> None:
    """One batch, deletes first: all of it happens or none of it does."""
    if changes:
        ordered = sorted(changes, key=lambda c: c["Action"] != "DELETE")
        client.change_resource_record_sets(
            HostedZoneId=zone_id, ChangeBatch={"Comment": names.dns_comment, "Changes": ordered}
        )


def refused(e: Exception) -> bool:
    """Route 53 answered a ChangeResourceRecordSets call with a 4xx error (botocore's ClientError): a batch is all or
    nothing, so none of it was made. No answer, or a 5xx, may have come after the change was made. GetChange cannot
    settle that: it needs the change id, which came back only in the answer that was lost."""
    meta = getattr(e, "response", None)
    if not isinstance(meta, dict) or not meta.get("Error", {}).get("Code"):
        return False
    info = meta.get("ResponseMetadata", {})
    if info.get("RetryAttempts"):  # an earlier attempt may have landed: its answer is what was lost
        return False
    return int(info.get("HTTPStatusCode") or 400) < 500


def describe(rs: dict) -> str:
    target = (
        rs["AliasTarget"]["DNSName"] if "AliasTarget" in rs else ", ".join(v["Value"] for v in rs["ResourceRecords"])
    )
    return f"{rs['Type']} {rs['Name'].rstrip('.')} -> {target}"


def recorded(client, zone_id: str, name: str, owned: list[dict]) -> tuple[list[dict], list[dict]]:
    """The A and AAAA record sets for `name`, split into (exactly one this install recorded creating, any other)."""
    want, mine = _norm(name), {key(rs) for rs in owned} - {None}
    r = client.list_resource_record_sets(HostedZoneId=zone_id, StartRecordName=want, StartRecordType="A", MaxItems="10")
    ours, other = [], []
    for rs in r["ResourceRecordSets"]:
        if _norm(rs["Name"]) == want and rs["Type"] in ("A", "AAAA"):
            (ours if key(rs) in mine else other).append(rs)
    return ours, other


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
