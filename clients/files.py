"""Download one authorized attachment without exposing credentials or overwriting files."""

import argparse
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clients import runenv
from clients.tico import APIError, Client


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file_id")
    parser.add_argument("destination", type=Path)
    args = parser.parse_args(argv)
    try:
        data = Client(runenv.require(os.environ, "HUB_API_URL"), runenv.require(os.environ, "HUB_TOKEN")).download(args.file_id)
        # Never use a remote display name as a local path; destination is explicit.
        fd = os.open(args.destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        except Exception:
            args.destination.unlink()
            raise
    except (APIError, OSError, ValueError, KeyError) as exc:
        parser.exit(1, "Attachment download failed: " + str(exc) + "\n")
    print(args.destination)


if __name__ == "__main__":
    main()
