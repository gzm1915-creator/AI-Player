"""Local Skeld room policy. The game still validates every walking route."""

from .spatial import ANCHOR_PREFIX_ROOMS, ROOMS

# Rooms joined by short corridors; this ranks destinations, never supplies a path.
NEIGHBORS = {
    "Cafeteria": {"Weapons", "Admin", "Storage", "MedBay", "UpperEngine"},
    "Weapons": {"Cafeteria", "O2", "Navigation"},
    "O2": {"Weapons", "Navigation", "Shields"},
    "Navigation": {"Weapons", "O2", "Shields"},
    "Shields": {"Navigation", "O2", "Communications", "Storage"},
    "Communications": {"Storage", "Shields"},
    "Storage": {"Cafeteria", "Admin", "Electrical", "LowerEngine", "Communications", "Shields"},
    "Admin": {"Cafeteria", "Storage"},
    "Electrical": {"Storage", "LowerEngine"},
    "LowerEngine": {"Electrical", "Storage", "Reactor", "Security"},
    "Reactor": {"LowerEngine", "Security", "UpperEngine"},
    "Security": {"Reactor", "UpperEngine", "LowerEngine"},
    "UpperEngine": {"Cafeteria", "MedBay", "Reactor", "Security"},
    "MedBay": {"Cafeteria", "UpperEngine"},
}


def room_of(anchor):
    room = anchor.get("roomId")
    if room in ROOMS:
        return room
    parts = anchor.get("anchorId", "").split(".")
    return ANCHOR_PREFIX_ROOMS.get(parts[1]) if len(parts) > 1 and parts[0] == "skeld" else None


class RoomSweep:
    REQUIRED_OTHER_ROOMS = 4

    def __init__(self):
        self.recent = []
        self.current = None
        self.next_task = None
        self.visits = {}

    def allowed(self, room):
        return room is None or room not in self.recent

    def arrive(self, room):
        if room not in NEIGHBORS or room == self.current:
            return False
        self.current = room
        self.visits[room] = self.visits.get(room, 0) + 1
        # Retain the four most recently visited distinct rooms. A room becomes
        # eligible only after four OTHER confirmed room arrivals.
        self.recent = [r for r in self.recent if r != room]
        self.recent.append(room)
        self.recent = self.recent[-self.REQUIRED_OTHER_ROOMS :]
        return True

    def nearby(self, room):
        return room in NEIGHBORS.get(self.current, ())

    def status(self):
        return {
            "currentRoom": self.current,
            "recentRooms": list(self.recent),
            "roomVisits": dict(self.visits),
            "requiredOtherRooms": self.REQUIRED_OTHER_ROOMS,
            "nextFakeTaskAnchorId": self.next_task,
        }
