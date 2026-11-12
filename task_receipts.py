"""Validate retained native completion facts against their original game/task."""

from datetime import datetime

SOURCE = "native_own_task_is_complete"
SOURCES = (SOURCE, "native_own_task_record_complete")


def timestamp(value):
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()


def valid_receipts(state, after=None):
    values = state.get("taskCompletions", [])
    if not isinstance(values, list) or type(state.get("self", {}).get("playerId")) is not int:
        return
    for receipt in values[:128]:
        if not isinstance(receipt, dict):
            continue
        task = receipt.get("task")
        if (
            receipt.get("source") not in SOURCES
            or not isinstance(task, dict)
            or task.get("complete") is not True
            or not isinstance(task.get("taskInstanceId"), str)
            or not task["taskInstanceId"]
            or type(task.get("taskId")) is not int
            or receipt.get("receiptId") != task["taskInstanceId"]
            or receipt.get("sessionId") != state.get("sessionId")
            or type(receipt.get("matchId")) is not int
            or type(receipt.get("playerId")) is not int
            or receipt.get("matchId") != state.get("matchId")
            or receipt.get("playerId") != state.get("self", {}).get("playerId")
        ):
            continue
        try:
            completed = timestamp(receipt.get("completedAtUtc"))
            age = timestamp(state.get("telemetryAt")) - completed
            if not -0.5 <= age <= 300 or (after and completed <= timestamp(after)):
                continue
        except (ValueError, TypeError, OverflowError):
            continue
        yield receipt


def receipt_for(state, task):
    instance = task.get("taskInstanceId")
    if not instance:
        return None
    return next(
        (
            r
            for r in valid_receipts(state)
            if r["task"]["taskInstanceId"] == instance
            and r["task"].get("taskId") == task.get("taskId")
            and r["task"].get("taskType") == task.get("taskType")
        ),
        None,
    )
