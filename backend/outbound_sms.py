# pyright: strict
"""Twilio and Close send one text through the outbound gate and keep the provider's receipt."""

import json
import math
import re
from collections.abc import Mapping
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING, Any, NamedTuple, cast
from urllib.parse import urlencode

from . import egress as _egress
from . import tool_services as _tool_services
from . import tool_templates as TT
from .entities.store import PHONE_CONTACT
from .store import H as _H
from .store import Problem
from .tool_services import RECORD_INTEGRATION, SHARED_KEY

if TYPE_CHECKING:
    from .outbound_actions import SafeRejection

H: Any = _H
TS: Any = _tool_services
EGRESS: Any = _egress
SERVICES = {"sms": "twilio", "close_sms": "close-crm"}
ACCOUNT_SID = re.compile(r"AC[0-9a-fA-F]{32}")
LEAD_ID = re.compile(r"lead_[a-zA-Z0-9]{1,100}")
TWILIO_SMS_ID = re.compile(r"(?:SM|MM)[0-9a-fA-F]{32}")
CLOSE_SMS_ID = re.compile(r"acti_[a-zA-Z0-9]{1,100}")
TRANSPORT: Any = None  # tests stand in for the provider at the network edge


class _Keys(NamedTuple):
    account: str
    credentials: dict[str, Any]
    way: Any = None


def _twilio_keys(c: Any, connection_id: str, tool: Any, keys: list[Any]) -> _Keys | None:
    if not tool["auth_template"]:
        account = str(keys[0]["username"] or "")
        if tool["auth_scheme"] == "basic_password" and ACCOUNT_SID.fullmatch(account):
            return _Keys(account, {"auth_token": keys[0]})
        return None
    spec = TT.spec_of(tool)
    if not spec or spec.get("id") != "twilio":
        return None
    accounts = {
        str(row["value"])
        for row in c.execute("SELECT name,value FROM tool_settings WHERE tool_id=?", (connection_id,))
        if (field := TT.field_for(spec, row["name"])) and field["id"] == "account_sid"
    }
    if len(accounts) != 1:
        return None
    account = accounts.pop()
    if not ACCOUNT_SID.fullmatch(account):
        return None
    by_field: dict[str, Any] = {}
    for key in keys:
        field = TT.field_for(spec, key["env"])
        if field and field["id"] != "account_sid":
            by_field.setdefault(field["id"], key)
    way = TT.choose(spec, {**by_field, "account_sid": account})
    return (
        _Keys(account, {name: by_field[name] for name in way["basic"] if name != "account_sid"}, way) if way else None
    )


def _keys(c: Any, channel: str, connection_id: str, actor: str) -> _Keys:
    from . import api_tools as _api_tools
    from . import credentials as _credentials

    service = SERVICES[channel]
    base = TS.SERVICES[service]["base_url"]
    tool = c.execute(
        f"SELECT tl.* FROM tools tl WHERE tl.id=? AND tl.base_url=? AND {RECORD_INTEGRATION}", (connection_id, base)
    ).fetchone()
    keys: list[Any] = (
        c.execute(
            f"SELECT k.* FROM credentials k WHERE k.tool_id=? AND {SHARED_KEY} AND k.access_level='read_write' "
            "ORDER BY k.created_at,k.id",
            (connection_id,),
        ).fetchall()
        if tool
        else []
    )
    selected = None
    if keys:
        if channel == "sms":
            selected = _twilio_keys(c, connection_id, tool, keys)
        elif tool["auth_scheme"] == "basic_user" and not tool["auth_template"]:
            selected = _Keys("", {"api_key": keys[0]})
    if selected is None:
        raise Problem(
            "validation", f"Choose a {TS.SERVICES[service]['name']} record integration with a shared write key", 422
        )
    tools: Any = _api_tools
    credentials: Any = _credentials
    if actor != H.TICO_ACTOR:
        subject = tools.subject_of(c, actor)
        for key in selected.credentials.values():
            grant = credentials.effective_grant(c, key["id"], subject)
            if grant is None or grant["access_level"] != "read_write":
                raise Problem(
                    "forbidden", "Sending through this connection needs Read and write on its key in Tools", 403
                )
    return selected


def _validate(channel: str, destination: str, payload: Mapping[str, Any], operation: str) -> None:
    if operation not in ("", "send"):
        raise Problem("validation", "SMS uses the send operation", 422)
    sender = payload.get("from")
    if not PHONE_CONTACT.fullmatch(destination) or not isinstance(sender, str) or not PHONE_CONTACT.fullmatch(sender):
        raise Problem("validation", "SMS needs a destination and a 'from' number in E.164 format", 422)
    body = payload.get("body")
    maximum = 1_600 if channel == "sms" else 1_000
    unit = "characters" if channel == "sms" else "UTF-8 bytes"
    try:
        encoded = body.encode("utf-8") if isinstance(body, str) else b""
    except UnicodeError:
        encoded = b""
    if (
        not isinstance(body, str)
        or not encoded
        or not body.strip()
        or len(body if channel == "sms" else encoded) > maximum
    ):
        raise Problem("validation", f"SMS needs a nonblank 'body' of up to {maximum} {unit}", 422)
    if channel == "close_sms" and "lead_id" in payload:
        lead = payload["lead_id"]
        if not isinstance(lead, str) or not LEAD_ID.fullmatch(lead):
            raise Problem("validation", "Close SMS 'lead_id' must be a Close lead ID", 422)


def _bound_account(payload: Mapping[str, Any], keys: _Keys) -> None:
    if "account_sid" in payload and payload["account_sid"] != keys.account:
        raise Problem("connection_account", "The Twilio connection is no longer on the account this text is for", 409)


def prepare(
    c: Any,
    settings: Any,
    actor: str,
    destination: str,
    payload: Mapping[str, Any],
    *,
    channel: str,
    operation: str,
    connection_id: str,
    task_id: str | None = None,
) -> tuple[str, str]:
    _validate(channel, destination, payload, operation)
    keys = _keys(c, channel, connection_id, actor)
    if channel == "sms":
        _bound_account(payload, keys)
    return "external", destination


def freeze(c: Any, connection_id: str, destination: str) -> dict[str, str]:
    return {"account_sid": _keys(c, "sms", connection_id, H.TICO_ACTOR).account}


def _auth(c: Any, vault: Any, keys: _Keys) -> dict[str, str]:
    values = {name: str(vault.cipher.decrypt(c, row)) for name, row in keys.credentials.items()}
    if keys.way:
        return cast("dict[str, str]", TT.render(keys.way, {**values, "account_sid": keys.account})[0])
    if keys.account:
        return cast("dict[str, str]", TS.apply_auth("basic_password", values["auth_token"], keys.account)[0])
    return cast("dict[str, str]", TS.apply_auth("basic_user", values["api_key"])[0])


def _retry_after(value: str) -> float:
    try:
        seconds = float(value)
    except ValueError:
        try:
            when = parsedate_to_datetime(value)
            seconds = (when.astimezone(timezone.utc) - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return 60.0
    return max(0.0, seconds) if math.isfinite(seconds) and seconds < 10**9 else 60.0


def dispatch(store: Any, row: Mapping[str, Any]) -> "dict[str, str] | SafeRejection":
    from . import outbound_actions as OA

    channel = row["channel"]
    vendor = TS.SERVICES[SERVICES[channel]]
    payload = OA.content(row)["payload"]
    vault = OA.vault()
    if vault is None:
        return OA.SafeRejection(60, "The credential vault is unavailable")
    try:
        with store.transaction() as c:
            # The grant check and the selection of the credentials this send actually uses share a transaction.
            keys = _keys(c, channel, row["connection_id"], row["actor"])
            if channel == "sms":
                _bound_account(payload, keys)
            headers = _auth(c, vault, keys)
    except Problem as exc:
        problem: Any = exc
        raise OA.Refused(str(problem.detail)) from None
    except Exception:  # noqa: BLE001 - no provider call was made and vault error details must stay private
        return OA.SafeRejection(60, "The connection's key could not be read")
    if channel == "sms":
        path = f"/2010-04-01/Accounts/{keys.account}/Messages.json"
        body = urlencode({"To": row["destination"], "From": payload["from"], "Body": payload["body"]}).encode()
        content_type, id_key = "application/x-www-form-urlencoded", "sid"
        id_shape = TWILIO_SMS_ID
    else:
        path = "/api/v1/activity/sms/"
        body = json.dumps(
            {
                "status": "outbox",
                **({"lead_id": payload["lead_id"]} if "lead_id" in payload else {}),
                "local_phone": payload["from"],
                "remote_phone": row["destination"],
                "text": payload["body"],
            }
        ).encode()
        content_type, id_key = "application/json", "id"
        id_shape = CLOSE_SMS_ID
    try:
        answer = EGRESS.post(
            vendor["base_url"] + path,
            body,
            {**headers, "Content-Type": content_type, "Accept": "application/json"},
            transport=TRANSPORT,
            response_bytes=16 * 1024,
        )
    except Exception:  # noqa: BLE001 - the send may have landed, and transport details may contain private data
        raise RuntimeError(f"{vendor['name']} did not return an answer") from None
    status = answer.status
    if status == 429:
        return OA.SafeRejection(_retry_after(answer.headers.get("retry-after", "")), f"{vendor['name']} answered 429")
    if status in (400, 401, 403, 404, 422):
        raise OA.Refused(f"{vendor['name']} refused the SMS ({status})")
    if not 200 <= status < 300:
        raise RuntimeError(f"{vendor['name']} answered {status}")
    try:
        record: object = json.loads(answer.head)
    except ValueError:
        record = None
    provider_id = cast("dict[str, object]", record).get(id_key) if isinstance(record, dict) else None
    if not isinstance(provider_id, str) or not id_shape.fullmatch(provider_id):
        raise RuntimeError(f"{vendor['name']} returned no readable SMS receipt")
    return {"provider_id": provider_id}
