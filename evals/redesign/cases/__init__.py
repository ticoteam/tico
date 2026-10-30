"""The redesign scenarios, by PLAN §7 group: task caps (1-10), spend (11-15), inboxes (16-21), engine (22-31),
permissions (32-35, 46), strategy (36-38), migration (39-44). The coverage matrix is not listed here: run.py builds it per seed."""
from .caps import SCENARIOS as _caps
from .engine import SCENARIOS as _engine
from .migration import SCENARIOS as _migration
from .inboxes import SCENARIOS as _inboxes
from .permissions import SCENARIOS as _permissions
from .recorder import SCENARIOS as _recorder
from .spend import SCENARIOS as _spend
from .strategy import SCENARIOS as _strategy

SCENARIOS = _caps + _spend + _inboxes + _engine + _permissions + _strategy + _migration + _recorder
