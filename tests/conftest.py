"""Faux site clubconnect pour les tests : aucune requête ne sort de la machine."""
from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from clubbot.signing import sign

FIXTURES = Path(__file__).parent / "fixtures"
CLUB = "67190ce4e464b78523032c8a"
AQUA_POWER_1845 = "6a998b4a255bf80f6dbf9e69"


def load_fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeClock:
    def __init__(self, start: datetime):
        self.t = start

    def now(self) -> datetime:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += timedelta(seconds=seconds)

    def monotonic(self) -> float:
        return self.t.timestamp()


class FakeSite:
    """Imite l'API : session par cookie, signature vérifiée, ouverture à notBefore."""

    def __init__(self, clock: FakeClock, seances: list[dict], *, fill_on_open: bool = False):
        self.clock = clock
        self.seances = copy.deepcopy(seances)
        self.fill_on_open = fill_on_open
        self.bookings: dict[str, list[dict]] = {}
        self.calls: list[tuple[datetime, str, str]] = []
        self.override: dict[str, httpx.Response] = {}

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def posts(self) -> list[tuple[datetime, str, str]]:
        return [c for c in self.calls if c[1] == "POST" and c[2] == "/api/mobile/booking"]

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append((self.clock.now(), request.method, path))
        assert request.headers["platform"] == "members"
        assert request.headers["x-signature"] == sign(request.method, path, request.headers["x-timestamp"])
        if path in self.override:
            return self.override[path]
        if path == "/api/mobile/session":
            email = json.loads(request.content)["email"]
            return httpx.Response(201, headers={"set-cookie": f"sid={email}; Path=/"})
        user = request.headers.get("cookie", "").removeprefix("sid=")
        if not user:
            return httpx.Response(401, json={"message": "unauthorized"})
        if path == "/api/mobile/seance":
            return httpx.Response(200, json=self.seances)
        if path == "/api/mobile/booking" and request.method == "GET":
            return httpx.Response(200, json=self.bookings.get(user, []))
        if path == "/api/mobile/booking" and request.method == "POST":
            body = json.loads(request.content)
            seance = next(s for s in self.seances if s["id"] == body["seance"]["id"])
            bookable = seance["bookable"]
            if self.clock.now() < datetime.fromisoformat(bookable["notBefore"]):
                return httpx.Response(400, json={"message": "NOT_YET_OPEN"})
            if self.fill_on_open:
                bookable["bookingsCount"] = bookable["bookingLimit"]
            if body["waiting"]:
                bookable["waitingCount"] += 1
            elif bookable["bookingsCount"] >= bookable["bookingLimit"]:
                return httpx.Response(400, json={"message": "FULL"})
            else:
                bookable["bookingsCount"] += 1
            self.bookings.setdefault(user, []).append(
                {"id": "b" + seance["id"], "canceled": False, "waiting": body["waiting"], "seance": {"id": seance["id"]}})
            return httpx.Response(201)
        return httpx.Response(404, json={"message": "not found"})


@pytest.fixture
def seances():
    return load_fixture("seance_2026-11-02.json")


@pytest.fixture
def clock():
    # lundi 19/10/2026 à 07:50 heure de Paris
    return FakeClock(datetime(2026, 10, 19, 5, 50, tzinfo=timezone.utc))
