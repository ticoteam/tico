"""Bot files never belong in the Tico checkout running this code."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def outside_installation(path):
    try:
        resolved = Path(path).expanduser().resolve()
        installation = ROOT.resolve()
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"Cannot check repository path: {path}") from exc
    inside = resolved.is_relative_to(installation)
    # Path.resolve on a case-insensitive Mac volume can keep the caller's spelling.
    if not inside:
        for parent in (resolved, *resolved.parents):
            try:
                if parent.samefile(installation):
                    inside = True
                    break
            except OSError:
                continue
    if inside:
        raise ValueError(f"Repository path is inside the Tico install: {path}")
    return resolved


def contained_file(root, path):
    """Check both the destination and its parents, including a dangling link's target."""
    root, path = Path(root).resolve(), Path(path)
    resolved = outside_installation(path)
    if not resolved.is_relative_to(root) or not path.parent.resolve().is_relative_to(root):
        raise ValueError(f"File path is outside the bot repo: {path}")
    # Replacing this entry must not traverse the final link, even when that link stays in the repo.
    return path.parent.resolve() / path.name
