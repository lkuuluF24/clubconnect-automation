"""Lecture des réponses de l'API : correspondance cours voulu / séance, état des places."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from .config import Course


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _norm(text: str) -> str:
    return " ".join(text.split()).casefold()


def find_seances(course: Course, seances: list[dict], tz: ZoneInfo) -> list[dict]:
    matches = []
    for seance in seances:
        local = parse_ts(seance["starts"]).astimezone(tz)
        if local.weekday() != course.weekday or local.strftime("%H:%M") != course.time:
            continue
        if course.name and _norm(seance.get("name", "")) != _norm(course.name):
            continue
        matches.append(seance)
    return matches


def block_reason(seance: dict) -> str | None:
    bookable = seance.get("bookable") or {}
    reason = (bookable.get("block") or {}).get("reason")
    if not bookable.get("enabled"):
        return reason or "réservation désactivée"
    return reason


def opens_at(seance: dict) -> datetime | None:
    value = (seance.get("bookable") or {}).get("notBefore")
    return parse_ts(value) if value else None


def is_full(seance: dict) -> bool:
    bookable = seance.get("bookable") or {}
    return bookable.get("bookingsCount", 0) >= bookable.get("bookingLimit", 0)


def waitlist_open(seance: dict) -> bool:
    bookable = seance.get("bookable") or {}
    return bookable.get("waitingCount", 0) < bookable.get("waitingLimit", 0)


def booking_state(seance_id: str, bookings: list[dict]) -> str | None:
    """'booked', 'waiting' ou None si aucune réservation active sur cette séance."""
    for booking in bookings:
        if booking.get("canceled") or (booking.get("seance") or {}).get("id") != seance_id:
            continue
        return "waiting" if booking.get("waiting") else "booked"
    return None
