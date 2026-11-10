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


def conflicts(client, zone_id: str, records: list[Record], allowed: set[str]) -> list[dict]:
    """The A, AAAA and CNAME record sets at the names in `records` that hold anything but these records' own values and
    `allowed` (this install's addresses): someone else's. An alias is always someone else's."""
    out = []
    for want in sorted({r.name.rstrip(".").lower() for r in records}):
        mine = allowed | {r.value for r in records if r.name.rstrip(".").lower() == want}
        r = client.list_resource_record_sets(
            HostedZoneId=zone_id, StartRecordName=want, StartRecordType="A", MaxItems="20"
        )
        for rs in r["ResourceRecordSets"]:
            if rs["Name"].rstrip(".").lower() != want or rs["Type"] not in ("A", "AAAA", "CNAME"):
                continue
            values = {v["Value"].rstrip(".") for v in rs.get("ResourceRecords", [])}
            if "AliasTarget" in rs or not values <= {v.rstrip(".") for v in mine}:
                out.append(rs)
    return out


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
