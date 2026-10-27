"""The same quarantine explanation for Health and the bot page, within existing review access."""

from .store import H


def resume_time(timing):
    """The UTC deadline shared by the full explanation and the compact task notice."""
    at = timing.get("resumes_at")
    return H.parse_ts(at) if at else None


def builder_notice(timing):
    if not timing["auto"]:
        return "Builder quarantined: until a person clears it"
    at = resume_time(timing)
    if not at:
        return "Builder quarantined: resumes after its cooldown"
    # An overnight cooldown must not look like an earlier time on the same day.
    since = timing.get("since")
    dated = since and H.parse_ts(since).date() != at.date()
    return "Builder quarantined: resumes " + at.strftime("%Y-%m-%d %H:%M UTC" if dated else "%H:%M UTC")


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

    at = resume_time(timing)
    # Include the date so a cooldown crossing midnight has an unambiguous UTC deadline.
    resumes = f"at {at.strftime('%Y-%m-%d %H:%M UTC')}" if at else "after its cooldown"
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
