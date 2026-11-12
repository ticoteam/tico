# pyright: strict
"""How long a bot's conversation stays cached between turns, for timers that wake a bot back into its session.

Claude Code caches a session's conversation for 1 hour. A turn that resumes within the hour reads it at 0.1x the
input price; one that comes back after the hour writes the whole conversation again at 2x. A timer set to exactly an
hour always lands just after the cache expired, so a timer that brings a bot back into an existing session "in about
an hour" uses CACHE_WARM_S instead: 50 minutes, which leaves 10 minutes for the wake to be claimed and started.
Timers meant to wait well beyond an hour keep their own values.
"""

CACHE_WARM_S = 50 * 60
