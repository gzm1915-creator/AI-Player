"""The sample culture runs in the game while the player is free to leave."""

def sample_phase(task):
    sample = task.get("sample") or {}
    if task.get("taskType") != "InspectSample" or not isinstance(sample, dict) or sample.get("supported") is not True:
        return None
    return sample.get("phase")


def sample_waiting(task):
    return sample_phase(task) == "processing"


def sample_actionable(task):
    return task.get("taskType") != "InspectSample" or sample_phase(task) in ("not_started", "ready")
