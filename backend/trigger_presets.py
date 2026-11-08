"""How a webhook trigger checks who sent a call, what kind of call it was, and what it may show.

Each preset is a small pure function `(headers, body, secret, now) -> (ok, reason)`: `headers` has
lower-case names, `body` is the exact bytes received, `secret` the trigger's signing secret and `now`
Unix seconds. Nothing here touches the database, so each can be tested with a published vector.

- `none`: the address's own secret is the only check.
- `hmac`: HMAC-SHA256 of the body as hex, in GitHub's `X-Hub-Signature-256` or a generic `X-Signature`
  (with or without `sha256=`). No timestamp.
- `github`: `X-Hub-Signature-256` only.
- `standard`: Standard Webhooks (standardwebhooks.com). The signed content is `<webhook-id>.<webhook-timestamp>.<body>`;
  `webhook-signature` holds one or more space-separated `v1,<base64>`; the secret is `whsec_<base64>` and the
  decoded bytes are the key. The timestamp must be within five minutes.
- `stripe`: `Stripe-Signature: t=<unix>,v1=<hex>[,v1=<hex>]`, HMAC-SHA256 over `<t>.<body>` with the secret
  string as the key. Within five minutes.
- `slack`: `X-Slack-Signature: v0=<hex>`, HMAC-SHA256 over `v0:<X-Slack-Request-Timestamp>:<body>` with Slack's signing
  secret as the key. Within five minutes. Slack's `url_verification` call is answered with its challenge
  (`slack_challenge`) before it counts as a delivery.
- `mercury`: `Mercury-Signature: t=<unix>,v1=<hex>`, HMAC-SHA256 over `<t>.<body>` with the endpoint's secret (Mercury
  returns it once when the webhook is made). Within five minutes. A Mercury trigger's deliveries go to Finance only.
- `stripe_finance`: the `stripe` check on a Finance-owned trigger: its deliveries go to Finance only, as doorbells.
- `intuit`: `intuit-signature: <base64>`, base64 of HMAC-SHA256 over the raw body with the app's verifier token. Finance's
  (QuickBooks, spec §10); a relayed delivery from Tico HQ was checked there and carries no signature here.
- `xero`: `x-xero-signature: <base64>`, base64 of HMAC-SHA256 over the raw body with the app's webhook key. Finance's.
"""
import base64
import binascii
import hashlib
import hmac
import json
import re
import secrets

PRESETS = ("none", "hmac", "github", "standard", "stripe", "slack", "mercury", "stripe_finance", "intuit", "xero")
# A trigger on one of these is Finance's (spec §3.4): its calls go to Finance, and only a finance admin manages it.
FINANCE = ("mercury", "stripe_finance", "intuit", "xero")
# Presets Tico HQ checks: it holds Intuit's and Xero's signing keys, and the install never does (spec §3.4). Such a call
# counts only when it came over the install's own Gateway relay (`via="gateway"`); the install's address refuses it.
HQ_CHECKED = ("intuit", "xero")
TOLERANCE = 300                        # seconds a timestamped signature stays good, either side of now
TYPE_FROM = {"github": "X-GitHub-Event", "stripe": "/type", "standard": "/type",
             "slack": "/event/type|/type", "mercury": "/resourceType",
             "stripe_finance": "/type"}      # a preset's default `type_from`; `a|b` tries a, then b
MAX_TYPE = 100
PREFIX = "whsec_"

# Headers that carry a credential or a signature: shown as "[redacted]" in any listing or page.
# Any header whose lower-cased name contains one of these words (contract C5), plus the cookie headers; the sender's own
# delivery id stays visible because it names the call.
SECRET_NAMES = ("signature", "authorization", "token", "secret", "key", "password")
SECRET_HEADERS = {"cookie", "set-cookie"}
VISIBLE_HEADERS = {"idempotency-key"}
# Not kept at all: a cookie is the sender's session, hop-by-hop headers belong to one connection, cf-* and cdn-loop
# are the edge's.
HOP_BY_HOP = {"cookie", "connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer",
              "trailers", "transfer-encoding", "upgrade", "cdn-loop"}
MAX_HEADER_VALUE = 2000


def _constant(sent, want):
    return hmac.compare_digest(sent.encode(), want.encode())


def _hex_ok(headers, names, key, body):
    """Whether one of `names` holds the hex HMAC-SHA256 of `body` under `key`, with or without `sha256=`."""
    want = hmac.new(key.encode(), body, hashlib.sha256).hexdigest()
    for name in names:
        sent = str(headers.get(name) or "").strip()
        if sent[:7].lower() == "sha256=":
            sent = sent[7:]
        if sent and _constant(sent.lower(), want):
            return True
    return False


def _hmac(headers, body, secret, now):
    names = ("x-hub-signature-256", "x-signature")
    if not any(headers.get(n) for n in names):
        return False, "This trigger needs a signed body (X-Hub-Signature-256 or X-Signature)"
    if not _hex_ok(headers, names, secret, body):
        return False, "The signature does not match the body and this trigger's signing secret"
    return True, ""


def _github(headers, body, secret, now):
    if not headers.get("x-hub-signature-256"):
        return False, "This trigger needs GitHub's X-Hub-Signature-256 header"
    if not _hex_ok(headers, ("x-hub-signature-256",), secret, body):
        return False, "The signature does not match the body and this trigger's signing secret"
    return True, ""


def standard_key(secret):
    """The HMAC key inside a `whsec_<base64>` secret, or None when it is not one."""
    if not secret.startswith(PREFIX):
        return None
    try:
        key = base64.b64decode(secret[len(PREFIX):], validate=True)
    except (binascii.Error, ValueError):
        return None
    return key or None


def _stamp(value, now):
    """Why a timestamp is unusable, or None."""
    try:
        sent = int(str(value).strip())
    except ValueError:
        return "The timestamp is not a number"
    if abs(now - sent) > TOLERANCE:
        return "The timestamp is more than 5 minutes from now"
    return None


def _standard(headers, body, secret, now):
    ident, stamp, sent = (str(headers.get(n) or "").strip() for n in ("webhook-id", "webhook-timestamp", "webhook-signature"))
    if not (ident and stamp and sent):
        return False, "This trigger needs webhook-id, webhook-timestamp and webhook-signature headers"
    key = standard_key(secret)
    if key is None:
        return False, "This trigger's signing secret is not a whsec_ secret"
    if (why := _stamp(stamp, now)):
        return False, why
    want = base64.b64encode(hmac.new(key, f"{ident}.{stamp}.".encode() + body, hashlib.sha256).digest()).decode()
    for part in sent.split():
        version, _, value = part.partition(",")
        if version == "v1" and _constant(value, want):
            return True, ""
    return False, "The signature does not match the body and this trigger's signing secret"


def _stripe(headers, body, secret, now, header_name="stripe-signature", shown="Stripe-Signature"):
    header = str(headers.get(header_name) or "").strip()
    if not header:
        return False, f"This trigger needs a {shown} header"
    pairs = [part.strip().partition("=") for part in header.split(",")]
    stamps = [value for name, _, value in pairs if name == "t"]
    if not stamps:
        return False, f"The {shown} header has no timestamp"
    if (why := _stamp(stamps[0], now)):
        return False, why
    want = hmac.new(secret.encode(), f"{stamps[0]}.".encode() + body, hashlib.sha256).hexdigest()
    if any(name == "v1" and _constant(value.lower(), want) for name, _, value in pairs):
        return True, ""
    return False, "The signature does not match the body and this trigger's signing secret"


def _mercury(headers, body, secret, now):
    """Mercury signs as Stripe does, under its own header name."""
    return _stripe(headers, body, secret, now, "mercury-signature", "Mercury-Signature")


def _b64_signed(headers, body, secret, header, shown):
    """A base64 HMAC-SHA256 of the raw body, as Intuit and Xero send it: the whole body is signed, no timestamp."""
    sent = str(headers.get(header) or "").strip()
    if not sent:
        return False, f"This trigger needs a signed body ({shown})"
    want = base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()
    if _constant(sent, want):
        return True, ""
    return False, "The signature does not match the body and this trigger's signing secret"


def _intuit(headers, body, secret, now):
    return _b64_signed(headers, body, secret, "intuit-signature", "Intuit-Signature")


def _xero(headers, body, secret, now):
    return _b64_signed(headers, body, secret, "x-xero-signature", "X-Xero-Signature")


def _slack(headers, body, secret, now):
    sent, stamp = (str(headers.get(n) or "").strip() for n in ("x-slack-signature", "x-slack-request-timestamp"))
    if not (sent and stamp):
        return False, "This trigger needs Slack's X-Slack-Signature and X-Slack-Request-Timestamp headers"
    if (why := _stamp(stamp, now)):
        return False, why
    want = "v0=" + hmac.new(secret.encode(), f"v0:{stamp}:".encode() + body, hashlib.sha256).hexdigest()
    if _constant(sent.lower(), want):
        return True, ""
    return False, "The signature does not match the body and this trigger's signing secret"


def slack_challenge(body):
    """The challenge when `body` is Slack's `url_verification` handshake, else None."""
    try:
        found = json.loads(body.decode("utf-8", "replace"))
    except ValueError:
        return None
    if isinstance(found, dict) and found.get("type") == "url_verification" and isinstance(found.get("challenge"), str):
        return found["challenge"][:1000]
    return None


VERIFY = {"hmac": _hmac, "github": _github, "standard": _standard, "stripe": _stripe, "slack": _slack,
          "mercury": _mercury, "stripe_finance": _stripe, "intuit": _intuit, "xero": _xero}


def verify(preset, headers, body, secret, now):
    """`(ok, reason)` for one call. `none` always passes: the address's own secret was the check."""
    check = VERIFY.get(preset)
    return (True, "") if check is None else check(headers, body, secret, now)


def new_secret(preset):
    """A signing secret in the shape the preset's sender uses."""
    if preset == "standard":
        return PREFIX + base64.b64encode(secrets.token_bytes(24)).decode()
    if preset in ("stripe", "stripe_finance"):
        return PREFIX + secrets.token_urlsafe(24)
    if preset == "slack":
        return secrets.token_hex(16)            # Slack's signing secrets are 32 hex characters
    return secrets.token_hex(32)


def secret_problem(preset, secret):
    """Why `secret` cannot serve `preset`, or None."""
    if len(secret) < 16:
        return "A signing secret has at least 16 characters"
    if preset == "standard" and standard_key(secret) is None:
        return "A Standard Webhooks secret looks like whsec_ followed by base64"
    return None


def lowered(headers):
    return {str(k).lower(): str(v) for k, v in dict(headers).items()}


def sender_id(headers):
    """The sender's own delivery id, if it sent one (contract order)."""
    for name in ("webhook-id", "x-github-delivery", "idempotency-key", "x-request-id"):
        if headers.get(name):
            return str(headers[name])[:200]
    return ""


def body_id(preset, body):
    """The sender's delivery id when it travels in the body: Stripe's `evt_...` or Mercury's event id at `/id`, the
    same for a resend."""
    if preset not in ("stripe", "stripe_finance", "mercury"):
        return ""
    try:
        found = json.loads(body.decode("utf-8", "replace")).get("id")
    except (ValueError, AttributeError):
        return ""
    shape = r"evt_[A-Za-z0-9_]{1,100}" if preset in ("stripe", "stripe_finance") else r"[A-Za-z0-9_-]{1,100}"
    return found if isinstance(found, str) and re.fullmatch(shape, found) else ""


def fallback_id(headers, body, received_at):
    """`h:` plus a hash of the minute and the body, so a bare retry within the minute is a duplicate."""
    minute = str(headers.get("webhook-timestamp") or received_at[:16])
    return "h:" + hashlib.sha256(minute.encode() + body).hexdigest()[:32]


def type_of(spec, headers, body):
    """The delivery's type: `spec` is a header name, or a JSON pointer when it starts with `/`. Empty when absent."""
    spec = (spec or "").strip()
    if "|" in spec:
        for one in spec.split("|"):
            if (found := type_of(one, headers, body)):
                return found
        return ""
    if not spec:
        return ""
    if spec.startswith("/"):
        try:
            node = json.loads(body.decode("utf-8", "replace"))
        except ValueError:
            return ""
        for token in spec[1:].split("/") if spec != "/" else [""]:
            token = token.replace("~1", "/").replace("~0", "~")
            if isinstance(node, dict) and token in node:
                node = node[token]
            elif isinstance(node, list) and token.isdigit() and int(token) < len(node):
                node = node[int(token)]
            else:
                return ""
        value = node
    else:
        value = headers.get(spec.lower())
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return ""
    return re.sub(r"[\x00-\x1f\x7f]", " ", str(value)).strip()[:MAX_TYPE]


def type_spec_problem(spec):
    spec = (spec or "").strip()
    if len(spec) > 100:
        return "type_from is at most 100 characters"
    if spec and not all(re.fullmatch(r"/\S*|[A-Za-z0-9-]+", one) for one in spec.split("|")):
        return "type_from is a header name or a JSON pointer starting with /, or several of them joined with |"
    return None


def kept_headers(headers, url_secret=None):
    """The headers worth keeping for a later look or replay: all but the connection's own, the edge's, a cookie and
    an Authorization that carries the address secret."""
    kept = {}
    for name, value in headers.items():
        if name in HOP_BY_HOP or name.startswith("cf-"):
            continue
        if name == "authorization" and url_secret and url_secret in value:
            continue
        kept[name] = value[:MAX_HEADER_VALUE]
    return kept


def redacted(headers):
    """Headers as a listing or page shows them: credential and signature values hidden."""
    return {name: "[redacted]" if _secret(name) else value for name, value in headers.items()}


def _secret(name):
    name = str(name).lower()
    return name not in VISIBLE_HEADERS and (name in SECRET_HEADERS or any(word in name for word in SECRET_NAMES))
