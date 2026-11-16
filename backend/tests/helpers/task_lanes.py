"""Helpers that other tests share, moved out of test_task_lanes.py so they import no test file."""

from backend.tests.helpers.api import assign, post, runner
from backend import bot_settings as BS

# What every runner at the minimum release (0.6.0-rc.1) advertises: runner-rs/src/lanes.rs CAPABILITIES.
RUNNER_CAPABILITIES = [
    "task_lanes_v2",
    "session_roots_v1",
    "task_goals_v1",
    "capacity_continue_v1",
    "source_continue_v1",
    "model_sign_out_v1",
    "model_keys_v1",
    "model_switch_type_v1",
    "model_remove_v1",
    "model_switch_to_key_v1",
    "model_connect_v1",
    "turns_at_once_v1",
    "private_lanes_v1",
    "model_subscriptions_v1",
    "model_subscription_activation_v1",
    "browser_exit_v1",
    "memory_tidy_packets_v1",
    "threads_v1",
    "memory_tidy_keep_v1",
    "harness_allocation_v1",
    "make_page",
]


def task_bot(api, lanes, capacity=8, others=()):
    """`ops` keyed on tasks, assigned to a runner that advertises the minimum release's capabilities (or none)."""
    with api.app.state.store.transaction() as c:
        for bot in ("ops", *others):
            config = BS.read(c, bot)
            BS.write(c, bot, {**config, 'session': 'task'})
    machine = runner(api)
    for bot in ("ops", *others):
        assign(api, machine, bot)
    post(
        api,
        "runners/heartbeat",
        {
            "version": "test",
            "platform": "test",
            "capacity": capacity,
            "readiness": {bot: True for bot in ("ops", *others)},
            "capabilities": RUNNER_CAPABILITIES if lanes else [],
        },
        token=machine["token"],
    )
    return machine


def new_task(api, title, owner="ops"):
    return post(api, "tasks", {"owner": owner, "title": title, "body": "Please do " + title + "."})
