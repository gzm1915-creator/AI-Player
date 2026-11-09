"""Short local motion forecasts. Estimates never enter the live player list."""
import math
from datetime import datetime, timezone


def xy(point):
    return (isinstance(point, dict) and
            all(type(point.get(k)) in (int, float) and math.isfinite(point[k]) for k in ("x", "y")))


def distance(a, b):
    return math.hypot(a["x"] - b["x"], a["y"] - b["y"])


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


class PlayerForecast:
    TTL = 3.0
    LEAD = .4
    MAX_SPEED = 6.0
    MAX_DISPLACEMENT = 4.5

    def __init__(self):
        self.clear()

    def clear(self):
        self.tracks = {}
        self.identity = None
        self.stamp = None
        self.sample_time = None
        self.visible_ids = set()

    def update(self, state, now):
        identity = (state.get("sessionId"), state.get("matchId"), state.get("mapId"), state.get("meetingId"))
        if identity != self.identity:
            self.clear()
            self.identity = identity
        if (not state.get("connected") or state.get("phase") != "TASKS" or state.get("meetingOpen")
                or state.get("ownRole", {}).get("isDead")
                or state.get("playerDetection", {}).get("status") != "ok"):
            self.tracks.clear()
            self.visible_ids.clear()
            self.stamp = None
            self.sample_time = None
            return
        stamp = state.get("telemetryAt")
        if stamp is not None and stamp == self.stamp:
            return  # Re-reading a cached frame must not refresh last-seen time.
        try:
            sample_time = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
            lag = max(0, (datetime.now(timezone.utc) - sample_time).total_seconds())
        except (ValueError, TypeError):
            sample_time, lag = None, 0
        if sample_time is not None and self.sample_time is not None and sample_time <= self.sample_time:
            return
        self.sample_time = sample_time
        self.stamp = stamp
        self.visible_ids = set()
        if lag > self.TTL:
            self.tracks.clear()
            return
        observed = now - lag
        for person in state.get("nearbyPlayers", []):
            pid = person.get("playerId")
            if type(pid) is not int or pid == state.get("self", {}).get("playerId"):
                continue
            if person.get("isDead") or person.get("inVent") or not xy(person):
                self.tracks.pop(pid, None)
                continue
            old = self.tracks.get(pid)
            dt = observed - old["seen"] if old else 0
            raw = (person.get("velocityX"), person.get("velocityY"))
            velocity = raw if all(number(v) for v in raw) else None
            jump = bool(old and dt > 0 and (distance(old["person"], person) > max(1.5, dt * 10)))
            if jump or dt > self.TTL:
                old, velocity = None, (0, 0)
            elif velocity is None and old and .04 <= dt <= 1:
                velocity = ((person["x"] - old["person"]["x"]) / dt,
                            (person["y"] - old["person"]["y"]) / dt)
            velocity = velocity or (0, 0)
            speed = math.hypot(*velocity)
            if speed > self.MAX_SPEED:
                velocity = tuple(v * self.MAX_SPEED / speed for v in velocity)
            if old and math.hypot(*velocity) > .2:
                velocity = tuple(.65 * new + .35 * previous for new, previous in zip(velocity, old["velocity"]))
            stopped = math.hypot(*velocity) <= .2
            still_since = old["stillSince"] if old and old["stillSince"] is not None and stopped and distance(old["person"], person) <= .15 else observed if stopped else None
            self.tracks[pid] = {"person": dict(person), "velocity": velocity, "seen": observed, "stillSince": still_since}
            self.visible_ids.add(pid)
        self.tracks = {pid: track for pid, track in self.tracks.items() if now - track["seen"] <= self.TTL}

    def get(self, pid, now, anchors=()):
        track = self.tracks.get(pid)
        if not track:
            return None
        age = now - track["seen"]
        if not 0 <= age <= self.TTL:
            return None
        person, velocity = track["person"], track["velocity"]
        visible = pid in self.visible_ids and age <= .6
        speed = math.hypot(*velocity)
        if not visible and speed <= .2:
            return None  # Do not follow a stationary ghost of the last sample.
        horizon = min(self.TTL, age + self.LEAD)
        # Decrease confidence in continued straight movement as sight is lost.
        travel_time = self.LEAD if visible else horizon - horizon * horizon / (2 * self.TTL)
        dx, dy = (v * travel_time for v in velocity)
        displacement = math.hypot(dx, dy)
        if displacement > self.MAX_DISPLACEMENT:
            dx, dy = dx * self.MAX_DISPLACEMENT / displacement, dy * self.MAX_DISPLACEMENT / displacement
        stationary = visible and track["stillSince"] is not None and now - track["stillSince"] >= 2
        task_stop = stationary and any(a.get("kind") == "task" and xy(a.get("position")) and
                                      distance(person, a["position"]) <= 1.25 for a in anchors)
        return {"playerId": pid, "playerName": person.get("playerName"), "colorName": person.get("colorName"),
                "x": person["x"] + dx, "y": person["y"] + dy,
                "lastObservedPosition": {"x": person["x"], "y": person["y"]},
                "velocityX": velocity[0], "velocityY": velocity[1], "speed": round(speed, 2),
                "visibleNow": visible, "positionSource": "motion_forecast", "predictionAgeSeconds": round(age, 2),
                "predictionTtlSeconds": self.TTL, "uncertaintyRadius": round(.25 + age * (.7 + speed * .6), 2),
                "stationary": stationary, "suspectedTaskStop": bool(task_stop),
                "notice": "预测仅供短时导航，不能当作实时目击、身份或击杀依据；停在任务点不证明正在做任务。"}

    def players(self, now, anchors=()):
        return [value for pid in self.tracks if (value := self.get(pid, now, anchors)) is not None]
