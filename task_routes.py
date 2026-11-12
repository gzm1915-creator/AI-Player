"""Match a task's console type before comparing its standing-point distance."""

# Legacy custom anchors without a known console suffix keep their old behavior.
# Built-in named consoles must not borrow a nearby task of a different kind.
CONSOLE_TASKS = {
    "upload-data": {"UploadData", "DownloadData"},
    "data": {"UploadData", "DownloadData"},
    "swipe-card": {"SwipeCard"},
    "asteroids": {"ClearAsteroids"},
    "clear-asteroids": {"ClearAsteroids"},
    "divert-power": {"DivertPower", "AcceptPower"},
    "med-bay": {"InspectSample"},
    "med-scanner": {"SubmitScan"},
    "unlock-manifolds": {"UnlockManifolds"},
    "garbage": {"EmptyGarbage", "EmptyChute"},
    "clean-filter": {"CleanO2Filter"},
    "stabilize-steering": {"StabilizeSteering"},
    "chart-course": {"ChartCourse"},
    "shield": {"PrimeShields"},
    "gas-can": {"FuelEngines"},
    "fuel-engine": {"FuelEngines"},
    "fix-wiring": {"FixWiring"},
    "calibrate": {"CalibrateDistributor"},
    "align-engine": {"AlignEngineOutput"},
    "start-reactor": {"StartReactor"},
    "vent-cleaning": {"VentCleaning"},
}


def anchor_accepts_task(anchor, task):
    declared = anchor.get("taskTypes")
    if isinstance(declared, list):
        return task.get("taskType") in declared
    parts = str(anchor.get("anchorId", "")).split(".")
    allowed = CONSOLE_TASKS.get(parts[2]) if len(parts) >= 3 and parts[0] == "skeld" else None
    return allowed is None or task.get("taskType") in allowed
