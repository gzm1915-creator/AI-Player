"""Recognize a real task refresh without treating a partial sample as a reset."""

REPAIRS = {"FixLights", "RestoreOxy", "FixComms", "ResetReactor"}


class TaskBatch:
    def __init__(self):
        self.identity = None
        self.catalog = {}
        self.epoch = 0

    def observe(self, state):
        identity = (state.get("sessionId"), state.get("matchId"), state.get("mapId"))
        if identity != self.identity:
            self.identity, self.catalog, self.epoch = identity, {}, 0
        current = {
            (str(t.get("taskId")), str(t.get("taskType"))): dict(t)
            for t in state.get("tasks", [])
            if t.get("taskType") not in REPAIRS
        }
        old, new = set(self.catalog), set(current)
        added, removed = new - old, old - new
        changed_type = bool({key[0] for key in added} & {key[0] for key in removed})
        replaced = bool(old and new and added and not old & new)
        reinitialized = any(
            self.catalog[k].get("taskInstanceId")
            and current[k].get("taskInstanceId")
            and self.catalog[k]["taskInstanceId"] != current[k]["taskInstanceId"]
            for k in old & new
        )
        progress_reset = (
            bool(new)
            and new == old
            and not any(t.get("taskInstanceId") for t in current.values())
            and all(not t.get("complete") and t.get("step", 0) == 0 for t in current.values())
            and any(t.get("complete") for t in self.catalog.values())
        )
        if changed_type or replaced or progress_reset or reinitialized:
            self.epoch += 1
            self.catalog = current
        else:
            self.catalog.update(current)
        return (*identity, self.epoch)
