"""Shared, side-effect-free validation for routine snapshots from Git or a runner."""
import hashlib
import re
from datetime import datetime
from pathlib import PurePosixPath
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter

DEFAULT_ZONE = 'America/Los_Angeles'
MAX_CONTENT = 100_000
MAX_SNAPSHOT = 1_000_000
# An event a routine may run `on:` instead of a cron. Tico emits these (docs/routines.md).
# `recording.ready` is the old name of `meeting.ready`; Tico still emits both.
EVENTS = ('meeting.ready', 'recording.ready', 'market.insight.urgent', 'inbound.received')
# A webhook trigger's event (backend/triggers.py); the server checks that the trigger exists.
WEBHOOK_EVENT = re.compile(r'webhook\.[a-z0-9][a-z0-9-]{0,39}')


def routine_path(value):
    if not isinstance(value, str) or len(value) > 300 or '\\' in value:
        raise ValueError('Template must be a repository-relative file path')
    path = PurePosixPath(value)
    if value and (path.is_absolute() or '..' in path.parts or any(ord(c) < 32 for c in value)):
        raise ValueError('Template must be a repository-relative file path')
    return value


def validate_schedules(entries, read_file=None):
    if entries is None:
        entries = []
    if not isinstance(entries, list) or len(entries) > 50:
        raise ValueError('Schedules must be a list of at most 50 routines')
    result, keys, total = [], set(), 0
    for index, entry in enumerate(entries, 1):
        if not isinstance(entry, dict):
            raise ValueError(f'Routine {index} must be an object')
        if set(entry) - {'id', 'cron', 'on', 'title', 'template', 'labels', 'timezone', 'instructions', 'enabled'}:
            raise ValueError(f'Routine {index} has unsupported fields')
        # A validated entry carries both keys, one of them empty; empty means absent.
        title, cron, on = entry.get('title'), entry.get('cron') or None, entry.get('on') or None
        if not isinstance(title, str) or not 1 <= len(title.strip()) <= 300:
            raise ValueError(f'Routine {index} needs a title of 1–300 characters')
        title = title.strip()
        if (cron is None) == (on is None):
            raise ValueError(f'Routine {index} needs exactly one of cron (a time) or on (an event)')
        if on is not None:
            if on not in EVENTS and not (isinstance(on, str) and WEBHOOK_EVENT.fullmatch(on)):
                raise ValueError(f'Routine {index} has an unknown event; Tico emits {", ".join(EVENTS)} '
                                 'and webhook.<trigger id> for a trigger in Settings > Triggers')
            cron = ''
        elif not isinstance(cron, str) or len(cron) > 100 or len(cron.split()) != 5 or not croniter.is_valid(cron):
            raise ValueError(f'Routine {title}: cron must use five fields, for example 0 9 * * 1-5')
        zone = entry.get('timezone', DEFAULT_ZONE)
        try:
            if not isinstance(zone, str) or len(zone) > 100:
                raise ValueError()
            tz = ZoneInfo(zone)
        except (ValueError, ZoneInfoNotFoundError, KeyError) as exc:
            raise ValueError(f'Routine {title}: {zone} is not a time zone. Use America/Los_Angeles') from exc
        if cron:
            try:
                croniter(cron, datetime(2026, 1, 1, tzinfo=tz), day_or=False).get_next(datetime)
            except ValueError as exc:
                raise ValueError(f'Routine {title}: cron has no practical occurrence; use five fields, for example 0 9 * * 1-5') from exc
        # Setup turns on the first routine; later changes use the owner's requested schedule.
        enabled = entry.get('enabled', True)
        if not isinstance(enabled, bool):
            raise ValueError(f'Routine {index} needs enabled to be true or false')
        key = entry.get('id')
        if key is None:
            key = 'title-' + hashlib.sha256(title.encode()).hexdigest()[:32]
        if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}', key):
            raise ValueError(f'Routine {index} needs a stable id using letters, numbers, dots, dashes or underscores')
        if key in keys:
            raise ValueError(f'Routine {index} has a duplicate id (give same-title routines distinct ids)')
        keys.add(key)
        template = routine_path(entry.get('template', ''))
        labels = entry.get('labels', [])
        if not isinstance(labels, list) or len(labels) > 20 or any(not isinstance(x, str) or not 1 <= len(x) <= 100 for x in labels):
            raise ValueError(f'Routine {index} needs at most 20 string labels of 1–100 characters')
        instructions = read_file(template) if template and read_file else entry.get('instructions', '')
        if not isinstance(instructions, str) or len(instructions.encode()) > MAX_CONTENT:
            raise ValueError(f'Routine {index} instructions exceed 100 KB')
        if template and not instructions.strip():
            raise ValueError(f'Routine {index} needs the template content before it can be saved')
        total += len(instructions.encode())
        if total > MAX_SNAPSHOT:
            raise ValueError('Routine instructions exceed the 1 MB snapshot limit')
        result.append(dict(id=key, title=title, cron=cron.strip(), on=on or '', timezone=zone,
                           template=template, labels=labels, instructions=instructions, enabled=enabled))
    return result
