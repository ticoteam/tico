"""The screening recorder: sends each corpus input once through every door it fits, at Hold, to the real decision model,
and keeps each answer as recordings/<key>.json, which the replay reads back (and grades, like --live).

    python -m evals.screening.record [--force]

A call's key is a hash of what the model was asked (the source and the fenced text) and the level, so each (input, door)
pair is one recording: a webhook's payload, a mail's subject and body, an idea's title and summary and a watcher's title
and body are different text. A recording that is there is kept unless --force. It needs live credentials (HUB_API_URL
and HUB_TOKEN, or TYPESAFE_API_KEY); without them nothing is sent and the exit code is 2."""
import argparse
import sys

from . import harness as X
from .run import NO_CREDENTIALS, live, live_engine


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--force", action="store_true", help="answer again even where a recording exists")
    args = parser.parse_args(argv)
    engine = live_engine()
    if engine is None:
        print(NO_CREDENTIALS.replace("evals.screening.run --live", "evals.screening.record"), file=sys.stderr)
        return 2
    kept = {"force": args.force}
    code, _ = live(X.load_corpus(), engine, record_to=kept)
    print(f"\n{len(kept.get('saved', []))} recorded, {len(kept.get('skipped', []))} kept as they were, in {X.RECORDINGS}")
    return code


if __name__ == "__main__":
    sys.exit(main())
