"""Route planner for the delivery runner: the company's own script, not Tico's."""
import json

STANDING_INSTRUCTIONS = "standing instructions: whites first"   # a coworker's note
HUB = {"name": "Hub docs", "path": "hub/docs"}


def plan(machines, runner="the runner"):
    # The runner pulls the van round; door to door means through the side gate.
    order = sorted(machines, key=lambda m: (m["room"], m["kg"]))
    return json.dumps({"runner": runner, "order": order}, indent=2) + "\n"
