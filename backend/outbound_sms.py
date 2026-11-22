# pyright: strict
"""Twilio and Close send one text through the outbound gate and keep the provider's receipt."""

import json
import re
from collections.abc import Mapping
from functools import partial
from typing import TYPE_CHECKING, Any, NamedTuple, cast
from urllib.parse import urlencode

import httpx

from . import egress as _egress
from . import outbound_http
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
TWILIO_MESSAGES = re.compile(r"/2010-04-01/accounts/[^/]+/(?:sms/)?messages(?:\.json|\.xml)?(?:/|$)")
CLOSE_SMS = re.compile(r"/api/v1/activity/sms(?:\.json)?(?:/|$)")
SMS_PROXY_REFUSAL = "Queue this text through Outbound actions (tico outbound send)."
TRANSPORT: Any = None  # tests stand in for the provider at the network edge


def is_send_path(base_url: str, method: str, path: str) -> bool:
    """Whether this request can write to a provider's SMS send resources."""
    base = base_url.rstrip("/")
    path = re.sub(r"/+", "/", path).rstrip("/").lower()
    if base == TS.SERVICES["twilio"]["base_url"]:
        return method != "GET" and TWILIO_MESSAGES.match(path) is not None
    return (
        base == TS.SERVICES["close-crm"]["base_url"]
        and method in {"POST", "PUT", "PATCH"}
        and CLOSE_SMS.match(path) is not None
    )


class _Keys(NamedTuple):
    account: str
    credentials: dict[str, Any]
    way: Any = None


def register_adapters() -> None:
    from . import outbound_actions as OA

    for channel, service in SERVICES.items():
        OA.register(
            OA.Adapter(
                channel,
                partial(prepare, channel=channel),
                dispatch,
                freeze=freeze if channel == "sms" else None,
                approval_prompt="May I send this text message?",
                action_label=lambda destination: f"text to {destination}",
                provider_name=TS.SERVICES[service]["name"],
            )
        )


def _twilio_keys(c: Any, connection_id: str, tool: Any, keys: list[Any]) -> _Keys | None:
    if not tool["auth_template"]:
        account = str(keys[0]["username"] or "")
        if tool["auth_scheme"] == "basic_password" and ACCOUNT_SID.fullmatch(account):
            return _Keys(account, {"auth_token": keys[0]})
        return None
    spec = TT.spec_of(tool)
    if not spec or spec.get("id") != "twilio":
        return None
    by_field: dict[str, Any] = {}
    for key in keys:
        field = TT.field_for(spec, key["env"])
        if field:
            by_field.setdefault(field["id"], key)
    account_key = by_field.get("account_sid")
    account = ""
    if account_key is None:
        accounts = {
            str(row["value"])
            for row in c.execute("SELECT name,value FROM tool_settings WHERE tool_id=?", (connection_id,))
            if (field := TT.field_for(spec, row["name"])) and field["id"] == "account_sid"
        }
        if len(accounts) != 1 or not ACCOUNT_SID.fullmatch(account := accounts.pop()):
            return None
    way = TT.choose(spec, {**by_field, "account_sid": account or account_key})
    if not way:
        return None
    selected = {name: by_field[name] for name in way["basic"] if name != "account_sid"}
    if account_key is not None:
        selected["account_sid"] = account_key
    return _Keys(account, selected, way)


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


def _bound_account(payload: Mapping[str, Any], account: str) -> None:
    if "account_sid" in payload and payload["account_sid"] != account:
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
    # A stored Account SID is frozen at enqueue and read again at dispatch, where a locked vault can requeue the send.
    if keys.account:
        _bound_account(payload, keys.account)
    return "external", destination


def freeze(c: Any, connection_id: str, destination: str) -> dict[str, str]:
    from . import outbound_actions as OA

    keys = _keys(c, "sms", connection_id, H.TICO_ACTOR)
    return {"account_sid": _account(c, OA.vault(), keys)}


def _account(c: Any, vault: Any, keys: _Keys) -> str:
    row = keys.credentials.get("account_sid")
    if row is None:
        return keys.account
    if vault is None:
        raise Problem("vault_unavailable", "The connection's key could not be read", 503)
    account = str(vault.cipher.decrypt(c, row))
    if not ACCOUNT_SID.fullmatch(account):
        raise Problem("validation", "The Twilio connection needs a valid Account SID", 422)
    return account


def _auth(c: Any, vault: Any, keys: _Keys, account: str) -> dict[str, str]:
    values = {
        name: str(vault.cipher.decrypt(c, row)) for name, row in keys.credentials.items() if name != "account_sid"
    }
    if keys.way:
        return cast("dict[str, str]", TT.render(keys.way, {**values, "account_sid": account})[0])
    if account:
        return cast("dict[str, str]", TS.apply_auth("basic_password", values["auth_token"], account)[0])
    return cast("dict[str, str]", TS.apply_auth("basic_user", values["api_key"])[0])


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
            # Contact policy, authority and the exact credential used share the final authorization transaction.
            keys = _keys(c, channel, row["connection_id"], row["actor"])
            policy = OA.decide(
                c, actor=row["actor"], channel=channel, audience="external", destination=row["destination"]
            )
            if policy["choice"] == "never":
                raise Problem("contact_blocked", policy["why"], 403)
            account = _account(c, vault, keys)
            if channel == "sms":
                _bound_account(payload, account)
            headers = _auth(c, vault, keys, account)
    except Problem as exc:
        problem: Any = exc
        if problem.code == "vault_unavailable":
            return OA.SafeRejection(60, "The connection's key could not be read")
        raise OA.Refused(str(problem.detail)) from None
    except Exception:  # noqa: BLE001 - no provider call was made and vault error details must stay private
        return OA.SafeRejection(60, "The connection's key could not be read")
    if channel == "sms":
        path = f"/2010-04-01/Accounts/{account}/Messages.json"
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
            response_bytes=64 * 1024,
            headers_only_statuses=(429,),
        )
    except Exception:  # noqa: BLE001 - the send may have landed, and transport details may contain private data
        raise RuntimeError(f"{vendor['name']} did not return an answer") from None
    status = answer.status
    if status == 429:
        asked = outbound_http.retry_after(httpx.Headers(answer.headers))
        return OA.SafeRejection(min(asked, 86_400) if asked is not None else 60, f"{vendor['name']} answered 429")
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
