"""`python -m backend.limited <scratch> <program> [args...]`: run one media tool (ffprobe, ffmpeg, pdftoppm) under hard
limits. The limits are set here, in a process of its own that then becomes the tool, never in a `preexec_fn` of the
threaded server, where code between fork and exec can deadlock. It imports nothing of Tico's."""
# pyright: strict

import os
import resource
import sys

MEMORY = 1536 * 1024**2  # address space: a 640 px poster of a 10 MB file needs far less
CPU_SECONDS = 20
FILE_BYTES = 10 * 1024**2  # every file the tool writes, its captured output included
ENV = {"PATH": "/usr/local/bin:/usr/bin:/bin", "LC_ALL": "C", "HOME": "/nonexistent"}


def limit() -> None:
    # macOS rejects address-space limits; the parent watches the tool's resident memory there instead.
    if sys.platform != "darwin":
        resource.setrlimit(resource.RLIMIT_AS, (MEMORY, MEMORY))
    resource.setrlimit(resource.RLIMIT_CPU, (CPU_SECONDS, CPU_SECONDS))
    resource.setrlimit(resource.RLIMIT_FSIZE, (FILE_BYTES, FILE_BYTES))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def main(argv: list[str]) -> int:
    if len(argv) < 2 or not os.path.isabs(argv[1]):
        sys.stderr.write("usage: python -m backend.limited <scratch directory> <absolute program path> [args...]\n")
        return 2
    scratch, program = argv[0], argv[1]
    os.chdir(scratch)
    limit()
    os.execve(program, argv[1:], ENV)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
