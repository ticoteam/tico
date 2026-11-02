"""`python -m runner.release_update`, as runner binaries of earlier releases start it."""
import sys

from runner_support.release_update import main

if __name__ == "__main__":
    sys.exit(main())
