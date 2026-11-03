import pytest

from backend import market

# backend/demo.py install() answers "ask the librarian" from the graph alone by replacing market.complete for the whole
# process. Demo apps are built in many modules, some from module-scoped fixtures, so keep the real function from before
# any test runs and put it back after every test. Without this, one demo test breaks every later market ask on its worker.
REAL_COMPLETE = market.complete


@pytest.fixture(autouse=True)
def _restore_market_complete():
    yield
    market.complete = REAL_COMPLETE
