"""Immutable MIME proofs and inert, complete previews of recipient-visible content."""

import base64
import hashlib
import json
import mimetypes
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses
from html.parser import HTMLParser

from .store import Problem

MAX_RAW = 24 * 1024 * 1024
MAX_PREVIEW = 256 * 1024
TEXT_MARKUP = frozenset("a abbr address article aside b big blockquote body br caption center cite code col colgroup "
                        "dd del div dl dt em figcaption figure footer h1 h2 h3 h4 h5 h6 head header hr html i ins kbd "
                        "li main mark nav ol p pre q rp rt ruby s samp section small span strong sub summary sup "
                        "table tbody td tfoot th thead title tr tt u ul var wbr".split())
TEXT_ATTRIBUTES = frozenset(("alt", "title", "href", "src", "value", "label", "placeholder", "aria-label"))


def digest(value):
    return hashlib.sha256(value if isinstance(value, bytes) else str(value).encode()).hexdigest()


class VisibleHTML(HTMLParser):
    """Keep all body text and link/image labels, including hidden text; never execute or render HTML."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text, self.ignored = [], 0
        self.complete = True

    def handle_starttag(self, tag, attrs):
        if (tag not in TEXT_MARKUP and tag != "script") or any(name not in TEXT_ATTRIBUTES for name, _ in attrs):
            self.complete = False
        if tag == "script":
            self.ignored += 1
        if not self.ignored:
            self.text.append("\n")
            self.text.extend(value for name, value in attrs if name in ("alt", "title", "href", "src", "style", "value", "label", "placeholder", "aria-label") and value)

    def handle_endtag(self, tag):
        if tag == "script" and self.ignored:
            self.ignored -= 1
        if not self.ignored:
            self.text.append("\n")

    def handle_comment(self, data):
        # Outlook can render conditional-comment bodies that ordinary HTML text extraction omits.
        if data.lstrip().lower().startswith("[if"):
            self.complete = False
            self.text.append("\n" + data + "\n")

    def unknown_decl(self, data):
        self.complete = False
        self.text.append("\n" + data + "\n")

    def handle_data(self, data):
        if not self.ignored:
            self.text.append(data)


def parsed(raw):
    data = base64.b64decode(raw + "=" * (-len(raw) % 4), altchars=b"-_", validate=True)
    if len(data) > MAX_RAW:
        raise ValueError("Email is too large")
    return BytesParser(policy=policy.default).parsebytes(data)


def body_parts(msg):
    """Alternative representations are bodies; mixed/related siblings are files, even when unnamed."""
    if msg.get_content_disposition() == "attachment" or msg.get_filename():
        return []
    if not msg.is_multipart():
        return [msg]
    children = list(msg.iter_parts())
    if msg.get_content_subtype() not in ("mixed", "related", "alternative"):
        return [msg]
    if msg.get_content_subtype() == "alternative":
        return [part for child in children for part in body_parts(child)]
    if msg.get_content_subtype() == "related":
        start = msg.get_param("start")
        root = next((child for child in children if start and child.get("Content-ID") == start), None)
        return body_parts(root if root is not None else children[0]) if children else []
    return body_parts(children[0]) if children else []


def file_parts(msg):
    leaves = [p for p in msg.walk() if not p.is_multipart()]
    nested = [p for p in msg.walk() if p.is_multipart() and (p.get_content_type() == "message/rfc822" or p.get_filename() or p.get_content_disposition() == "attachment")]
    return leaves + nested


def part_bytes(part):
    if part.get_content_type() == "message/rfc822" and part.is_multipart():
        return b"\n".join(message.as_bytes(policy=policy.SMTP) for message in part.get_payload())
    return part.as_bytes(policy=policy.SMTP) if part.is_multipart() else part.get_payload(decode=True) or b""


def snapshot(raw, mailbox):
    try:
        msg = parsed(raw)
        for key in ("from", "sender", "reply-to", "to", "cc", "bcc", "subject", "in-reply-to", "references"):
            if len(msg.get_all(key, [])) > 1:
                raise ValueError("Ambiguous repeated header")
        to = sorted({a.lower() for _, a in getaddresses(msg.get_all("to", []) + msg.get_all("cc", [])
                                                      + msg.get_all("bcc", [])) if a})
        sender = [a.lower() for _, a in getaddresses(msg.get_all("from", []))]
        from .mail_settings import ADDRESS
        if not to or len(to) > 100 or sender != [mailbox] or any(not ADDRESS.fullmatch(a) for a in to):
            raise ValueError("Invalid envelope")
        bodies, files, texts, visible = body_parts(msg), [], [], []
        complete = all(not body.is_multipart() for body in bodies)
        leaves = file_parts(msg)
        attached_children = {id(child) for parent in leaves if parent.is_multipart() for child in list(parent.walk())[1:]}
        for index, part in enumerate(leaves):
            if id(part) in attached_children:
                continue
            for key in ("content-type", "content-disposition", "content-transfer-encoding", "content-id"):
                if len(part.get_all(key, [])) > 1:
                    raise ValueError("Ambiguous MIME header")
            payload = part_bytes(part)
            is_body = any(part is body for body in bodies)
            supported = part.get_content_type() in ("text/plain", "text/html")
            unsupported_body = not supported and (is_body or (part.get_content_maintype() == "text"
                and not part.get_filename() and part.get_content_disposition() != "attachment"))
            if unsupported_body:
                complete = False
            if not is_body or not supported:
                name = str(part.get_filename() or f"part-{index + 1}" + (mimetypes.guess_extension(part.get_content_type()) or ".bin"))[:255]
                files.append({"name": name, "sha256": digest(payload), "size": len(payload),
                              "type": part.get_content_type(), "part": index})
                if not supported:
                    if unsupported_body and part.get_content_maintype() == "text":
                        try:
                            text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
                        except LookupError:
                            text = payload.decode("utf-8", errors="replace")
                        visible.append({"type": part.get_content_type(), "text": text})
                    continue
                if part.get_content_disposition() == "attachment" or part.get_filename():
                    continue
            try:
                text = payload.decode(part.get_content_charset() or "utf-8", errors="strict")
            except (UnicodeDecodeError, LookupError):
                text, complete = payload.decode("utf-8", errors="replace"), False
            if part.get_content_type() == "text/plain":
                if is_body:
                    texts.append(text)
                visible.append({"type": "text/plain", "text": text})
            else:
                html = VisibleHTML()
                html.feed(text)
                html.close()
                complete = complete and html.complete
                # Unknown rendering stays inert in the preview, with no discarded body or attribute bytes.
                visible.append({"type": "text/html", "text": "".join(html.text) if html.complete else text})
        if len(str(msg.get("subject", ""))) > 1000 or len(files) > 50 or sum(len(p["text"]) for p in visible) > MAX_PREVIEW:
            raise ValueError("The email cannot be previewed in full")
    except (ValueError, TypeError, LookupError) as exc:
        raise Problem("email", "Invalid email: From must match the inbox, with 1–100 valid recipients, at most 50 files "
                      "and at most 256 KiB of visible text", 422) from exc
    body = "\n".join(texts) if texts else "\n".join(p["text"] for p in visible)
    parts = [{"type": p.get_content_type(), "parameters": sorted((k, str(v)) for k, v in p.get_params()[1:] if k != "boundary") if p.get_params() else [],
              "name": p.get_filename(), "content_id": p.get("Content-ID"), "content_location": p.get("Content-Location"),
              "disposition": p.get_content_disposition(), "sha256": digest(part_bytes(p))}
             for p in leaves]
    material_headers = {key: str(msg.get(key) or "") for key in ("from", "sender", "reply-to", "in-reply-to", "references")}
    groups = {key: sorted(a.lower() for _, a in getaddresses(msg.get_all(key, [])) if a) for key in ("to", "cc", "bcc")}
    proof = {"subject": str(msg.get("subject", "")), "headers": material_headers, "recipient_headers": groups, "parts": parts}
    return {"recipients": to, "attachments": files, "subject": str(msg.get("subject", ""))[:1000],
            "body_sha256": digest(body), "body": body, "content_sha256": digest(json.dumps(proof, sort_keys=True, separators=(",", ":"))),
            "in_reply_to": str(msg.get("in-reply-to", "")), "references": str(msg.get("references", "")),
            "visible_bodies": visible, "classification_complete": complete, "recipient_headers": groups, "raw": raw}


def attachment(raw, index):
    leaves = file_parts(parsed(raw))
    if not 0 <= index < len(leaves):
        raise Problem("not_found", "File not found", 404)
    return part_bytes(leaves[index])
