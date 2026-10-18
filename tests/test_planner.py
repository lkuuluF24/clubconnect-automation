from zoneinfo import ZoneInfo

from clubbot import planner
from clubbot.config import Course
from clubbot.signing import sign

from conftest import AQUA_POWER_1845, load_fixture

PARIS = ZoneInfo("Europe/Paris")


def test_signature_matches_har_sample():
    assert sign("POST", "/api/mobile/session", "1792232604270").startswith("cfaea0c4")


def test_finds_course_in_paris_time(seances):
    # 18:45 à Paris le 02/11 (heure d'hiver) = 17:45Z
    matches = planner.find_seances(Course(0, "18:45", "aqua  power"), seances, PARIS)
    assert [s["id"] for s in matches] == [AQUA_POWER_1845]


def test_course_absent_on_that_day(seances):
    assert planner.find_seances(Course(0, "20:00", "AQUA POWER"), seances, PARIS) == []


def test_without_name_two_courses_can_match(seances):
    # AQUA FITNESS et CAF commencent tous deux à 10:15
    assert len(planner.find_seances(Course(0, "10:15"), seances, PARIS)) == 2


def test_block_reason(seances):
    by_name = {s["name"]: s for s in seances}
    assert planner.block_reason(by_name["CAF"]) == "NOT_BOOKABLE"
    assert planner.block_reason(by_name["AQUA CYCLING"]) is None


def test_full_and_waitlist(seances):
    seance = next(s for s in seances if s["id"] == AQUA_POWER_1845)
    assert not planner.is_full(seance)
    seance["bookable"]["bookingsCount"] = 30
    assert planner.is_full(seance)
    assert planner.waitlist_open(seance)


def test_booking_state():
    bookings = load_fixture("bookings.json")
    assert planner.booking_state("6a998b3c255bf80f6dbf9e5c", bookings) == "booked"
    assert planner.booking_state("inconnu", bookings) is None
    bookings[0]["canceled"] = True
    assert planner.booking_state("6a998b3c255bf80f6dbf9e5c", bookings) is None
