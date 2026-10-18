from datetime import date, datetime, timezone

import httpx

from clubbot.config import Account, Course, Settings
from clubbot.notify import Notifier
from clubbot.runner import ABSENT, ALREADY, BOOKED, DRY_RUN, FAILED, WAITING, Runner

from conftest import AQUA_POWER_1845, CLUB, FakeSite

MONDAY_1845 = Course(0, "18:45", "AQUA POWER")
OPENING = datetime(2026, 10, 19, 6, 0, tzinfo=timezone.utc)


class RecordingNotifier(Notifier):
    def __init__(self):
        self.sent = []

    def send(self, title, message, level="info"):
        self.sent.append((title, message, level))


def accounts(*courses_per_account):
    return [Account(f"user{i}", f"user{i}@example.org", "secret", list(courses))
            for i, courses in enumerate(courses_per_account)]


def run(clock, tmp_path, site, accts, **kw):
    settings = Settings(club_id=CLUB, lock_file=tmp_path / "blocked.json")
    notifier = RecordingNotifier()
    runner = Runner(settings, accts, notifier, transport=site.transport(),
                    now=clock.now, sleep=clock.sleep, clock=clock.monotonic, **kw)
    return runner, runner.run(), notifier


def statuses(outcomes):
    return [(o.account, o.status) for o in outcomes]


def test_books_every_account_right_after_opening(clock, tmp_path, seances):
    site = FakeSite(clock, seances)
    runner, outcomes, notifier = run(clock, tmp_path, site, accounts([MONDAY_1845], [MONDAY_1845]))

    assert statuses(outcomes) == [("user0", BOOKED), ("user1", BOOKED)]
    assert all(t >= OPENING for t, _, _ in site.posts())
    assert [p for _, _, p in site.calls].count("/api/mobile/seance") == 1
    # 2 connexions + 1 planning + 2 lectures + 2 réservations + 2 vérifications
    assert runner.guard.count == len(site.calls) == 9
    assert notifier.sent[0][2] == "info"


def test_requests_before_opening_are_only_reads(clock, tmp_path, seances):
    site = FakeSite(clock, seances)
    run(clock, tmp_path, site, accounts([MONDAY_1845]))
    before = [(m, p) for t, m, p in site.calls if t < OPENING]
    assert ("POST", "/api/mobile/booking") not in before


def test_nothing_wanted_that_day_sends_no_request(clock, tmp_path, seances):
    site = FakeSite(clock, seances)
    runner, outcomes, notifier = run(clock, tmp_path, site, accounts([Course(1, "19:00", "AQUA POWER")]))
    assert outcomes == [] and site.calls == [] and notifier.sent == []


def test_absent_course_is_logged_not_booked(clock, tmp_path, seances):
    site = FakeSite(clock, seances)
    _, outcomes, _ = run(clock, tmp_path, site, accounts([Course(0, "20:00", "AQUA POWER")]))
    assert statuses(outcomes) == [("user0", ABSENT)]
    assert [p for _, _, p in site.calls] == ["/api/mobile/session", "/api/mobile/seance"]


def test_extra_course_only_for_one_account(clock, tmp_path, seances):
    site = FakeSite(clock, seances)
    cycling = Course(0, "19:45", "AQUA CYCLING")
    _, outcomes, _ = run(clock, tmp_path, site, accounts([MONDAY_1845], [MONDAY_1845, cycling]))
    assert sorted((o.account, o.course, o.status) for o in outcomes) == [
        ("user0", str(MONDAY_1845), BOOKED),
        ("user1", str(cycling), BOOKED),
        ("user1", str(MONDAY_1845), BOOKED),
    ]


def test_already_booked_is_not_posted_again(clock, tmp_path, seances):
    site = FakeSite(clock, seances)
    site.bookings["user0@example.org"] = [{"canceled": False, "waiting": False, "seance": {"id": AQUA_POWER_1845}}]
    _, outcomes, _ = run(clock, tmp_path, site, accounts([MONDAY_1845]))
    assert statuses(outcomes) == [("user0", ALREADY)]
    assert site.posts() == []


def test_full_course_falls_back_to_waitlist(clock, tmp_path, seances):
    site = FakeSite(clock, seances, fill_on_open=True)
    _, outcomes, _ = run(clock, tmp_path, site, accounts([MONDAY_1845]))
    assert statuses(outcomes) == [("user0", WAITING)]
    assert len(site.posts()) == 2


def test_ambiguous_course_is_refused(clock, tmp_path, seances):
    site = FakeSite(clock, seances)
    _, outcomes, _ = run(clock, tmp_path, site, accounts([Course(0, "10:15")]))
    assert statuses(outcomes) == [("user0", FAILED)]
    assert site.posts() == []


def test_dry_run_never_posts(clock, tmp_path, seances):
    site = FakeSite(clock, seances)
    _, outcomes, _ = run(clock, tmp_path, site, accounts([MONDAY_1845]), dry_run=True, wait=False)
    assert statuses(outcomes) == [("user0", DRY_RUN)]
    assert site.posts() == []
    assert clock.now() < OPENING


def test_rate_limit_stops_everything_and_next_run_stays_offline(clock, tmp_path, seances):
    site = FakeSite(clock, seances)
    site.override["/api/mobile/seance"] = httpx.Response(429)
    runner, _, notifier = run(clock, tmp_path, site, accounts([MONDAY_1845], [MONDAY_1845]))
    assert runner.blocked and notifier.sent[0][2] == "error"
    assert len(site.calls) == 2

    site.calls.clear()
    runner, _, _ = run(clock, tmp_path, site, accounts([MONDAY_1845]))
    assert runner.blocked and site.calls == []


def test_wrong_password_only_fails_that_account(clock, tmp_path, seances):
    site = FakeSite(clock, seances)
    accts = accounts([MONDAY_1845], [MONDAY_1845])
    original = site.handle

    def handle(request):
        if request.url.path == "/api/mobile/session" and b"user0@" in request.content:
            site.calls.append((clock.now(), request.method, request.url.path))
            return httpx.Response(401, json={"message": "bad credentials"})
        return original(request)

    site.handle = handle
    _, outcomes, _ = run(clock, tmp_path, site, accts)
    assert sorted(statuses(outcomes)) == [("user0", FAILED), ("user1", BOOKED)]


def test_explicit_date(clock, tmp_path, seances):
    site = FakeSite(clock, seances)
    settings = Settings(club_id=CLUB, lock_file=tmp_path / "blocked.json")
    runner = Runner(settings, accounts([MONDAY_1845]), RecordingNotifier(), transport=site.transport(),
                    now=clock.now, sleep=clock.sleep, clock=clock.monotonic)
    assert statuses(runner.run(date(2026, 11, 2))) == [("user0", BOOKED)]
