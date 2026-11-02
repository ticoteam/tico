"""`python -m runner.engine prepare|path`, and `source_id`, for runners and update processes of earlier releases."""
import sys

from runner_support.engine import main, source_id  # noqa: F401

if __name__ == "__main__":
    sys.exit(main())
