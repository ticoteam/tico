"""Helpers that other tests share, moved out of test_runner_load.py so they import no test file."""

from backend.tests.helpers.performance import sample


def point(at, cpu=95, mem=0.5, active=3, load=None):
    return sample(at, cpu=cpu, mem=mem) | {
        "load": [load if load is not None else 1, 1, 1],
        "active": active,
        "swap_used": 20,
        "swap_total": 100,
    }
