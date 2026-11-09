"""Helpers that other tests share, moved out of test_performance.py so they import no test file."""


def sample(at, cpu=20.0, mem=0.5, procs=()):
    return {
        "at": at,
        "cpu": cpu,
        "cores": 8,
        "load": [1.0, 1.0, 1.0],
        "mem_total": 1000,
        "mem_used": int(1000 * mem),
        "disk": [{"volume": "state", "total_bytes": 100, "free_bytes": 50}],
        "active": 1,
        "procs": [{"name": n, "cpu": c, "mem": 10, "count": 1} for n, c in procs],
    }
