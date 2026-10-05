"""Tests read the registry from a fictional company, never from a real environment's."""
import os
from pathlib import Path

os.environ["TICO_REGISTRY_DIR"] = str(Path(__file__).parent / "backend/tests/fixtures/registry")
# The update check would otherwise call GitHub from every test that reads /api/v2/config.
os.environ["TICO_UPDATE_CHECK"] = "off"
# A runner readiness call would otherwise start real containers on whoever's computer runs the suite.
os.environ["TICO_RUNNER_CONTAINER_PROBE"] = "off"
# A test that misses a stub must fail, not act in whoever's AWS account runs the suite: one already
# created a real bucket. Fake keys and no profile or config files make any real AWS call unauthorized.
for _name in ("AWS_PROFILE", "AWS_DEFAULT_PROFILE", "AWS_SESSION_TOKEN", "AWS_ROLE_ARN", "AWS_WEB_IDENTITY_TOKEN_FILE"):
    os.environ.pop(_name, None)
os.environ.update({"AWS_ACCESS_KEY_ID": "testing", "AWS_SECRET_ACCESS_KEY": "testing",
                   "AWS_CONFIG_FILE": os.devnull, "AWS_SHARED_CREDENTIALS_FILE": os.devnull,
                   "AWS_EC2_METADATA_DISABLED": "true"})
