"""`python -m runner.cloud_machine boot`, as cloud machine images saved by earlier releases start it."""
import sys

from runner_support.cloud_machine import main

if __name__ == "__main__":
    sys.exit(main())
