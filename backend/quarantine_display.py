"""The same quarantine explanation for Health and the bot page, within existing review access."""

from .store import H


def explanation(c, slug, who, auth):
    timing = H.quarantine_resumes(c, slug)
    manages = auth is not None and (auth.operator(c, who, slug) or auth.bot_manager(c, who, slug))
    if not timing["auto"]:
        text = "Quarantined until a person clears it."
        review = H.quarantine_review(c, slug) if manages else None
        if review and review.get("found"):
            found = review["found"]
            found = "a secrets/ path" if found == "secrets/" else f"another bot's folder ({found})"
            what = review.get("what") or "write"
            article = "an" if what[0].lower() in "aeiou" else "a"
            text += f" The safety check stopped {article} {what} that named {found}. It matched text only; nothing was sent or changed."
        return text

    at = timing["resumes_at"]
    # Include the date so a cooldown crossing midnight has an unambiguous UTC deadline.
    resumes = f"at {H.parse_ts(at).strftime('%Y-%m-%d %H:%M UTC')}" if at else "after its cooldown"
    text = (f"Quarantined for repeated refused writes (rule 8; {H.quarantine_reason(c, slug)}): "
            f"resumes by itself {resumes}; a person who manages it can clear it sooner.")
    if manages:
        row = H._one(c, "SELECT detail_json FROM events WHERE action='quarantine' AND target=? "
                        "ORDER BY ts DESC LIMIT 1", (H.bot_actor(slug),))
        detail = (H._json(row["detail_json"], {}) or {}) if row else {}
        refused = detail.get("refusal") or {}
        if refused.get("rule") and refused.get("detail"):
            text += f" Refused write ({H.escape_preview(refused['rule'])}): {H.escape_preview(refused['detail'])}"
    return text
