# pyright: strict
"""Twilio and Close send one text through the outbound gate and keep the provider's receipt."""

import json
import re
from collections.abc import Mapping
from functools import partial
from typing import TYPE_CHECKING, Any, NamedTuple, cast
from urllib.parse import urlsplit, urlencode

from . import egress as _egress
from . import outbound_http
from . import tool_services as _tool_services
from . import tool_templates as TT
from .entities.store import PHONE_CONTACT
from .store import H as _H
from .store import Problem
from .tool_services import RECORD_INTEGRATION, SHARED_KEY

if TYPE_CHECKING:
    from .outbound_actions import DispatchStopped, SafeRejection

H: Any = _H
TS: Any = _tool_services
EGRESS: Any = _egress
SERVICES = {"sms": "twilio", "close_sms": "close-crm"}
ACCOUNT_SID = re.compile(r"AC[0-9a-fA-F]{32}")
LEAD_ID = re.compile(r"lead_[a-zA-Z0-9]{1,100}")
TWILIO_SMS_ID = re.compile(r"(?:SM|MM)[0-9a-fA-F]{32}")
CLOSE_SMS_ID = re.compile(r"acti_[a-zA-Z0-9]{1,100}")
SMS_PROXY_REFUSAL = (
    "This SMS connection is reserved for Outbound actions: it only reads here. Queue a text with tico outbound send,"
    " and use another connection for other writes."
)
TRANSPORT: Any = None  # tests stand in for the provider at the network edge


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").rstrip(".")


def provider_of(tool: Any) -> str | None:
    """The SMS provider a tool talks to ("twilio" or "close-crm"), by its host only (the provider's own domain or any
    subdomain: regional, bulk or MCP), else None. Not by service or name, which Tools may infer from a title."""
    host = _host(str(tool["base_url"] or ""))
    for name in SERVICES.values():
        root = _host(str(TS.SERVICES[name]["base_url"])).split(".", 1)[-1]  # twilio.com, close.com
        if host == root or host.endswith("." + root):
            return name
    return None


def reserved_for_queue(tool: Any, method: str) -> bool:
    """Whether the Tools proxy must refuse this request: an SMS provider's record integration is reserved for the
    outbound queue, so through the proxy it only reads (GET, HEAD). No path list to keep up with: every write, MCP call
    included, goes through the queue's policy, approval and opt-out checks or not at all."""
    return bool(tool["record_owner"]) and provider_of(tool) is not None and method.upper() not in {"GET", "HEAD"}


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
    base = _host(str(TS.SERVICES[service]["base_url"]))
    found = c.execute(f"SELECT tl.* FROM tools tl WHERE tl.id=? AND {RECORD_INTEGRATION}", (connection_id,)).fetchone()
    # The provider's own API host, however its address is spelled (case, trailing dot, an explicit :443).
    # The provider's own API host on its own port, however the address is spelled (case, trailing dot, :443).
    parts = urlsplit(str(found["base_url"] or "")) if found else None
    same = parts is not None and _host(parts.geturl()) == base and parts.port in (None, 443)
    tool: Any = found if same else None
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


def dispatch(store: Any, row: Mapping[str, Any]) -> "dict[str, str] | SafeRejection | DispatchStopped":
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
            stopped = OA.recheck_dispatch_policy(c, row, "external")
            if stopped is not None:
                return stopped
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
        answer: _egress.Answer = EGRESS.post(
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
        asked = outbound_http.retry_after(answer.headers)
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
