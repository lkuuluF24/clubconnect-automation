from datetime import date

import httpx
import pytest

from clubbot.client import ApiError, Blocked, ClubConnectClient, Guard

from conftest import CLUB


def make(clock, tmp_path, handler, **guard_kw):
    kw = dict(min_interval_s=1.5, max_requests=30, lock_file=tmp_path / "blocked.json", block_hours=24)
    kw.update(guard_kw)
    guard = Guard(**kw, clock=clock.monotonic, sleep=clock.sleep, now=clock.now)
    client = ClubConnectClient(CLUB, guard, label="test", transport=httpx.MockTransport(handler),
                               sleep=clock.sleep, clock_ms=lambda: int(clock.now().timestamp() * 1000))
    return guard, client


def test_seance_query_is_encoded_like_the_browser(clock, tmp_path):
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, json=[])

    _, client = make(clock, tmp_path, handler)
    client.seances(date(2026, 11, 2))
    assert seen == [f"https://members.clubconnect.fr/api/mobile/seance?clubs={CLUB}"
                    "&date_min=2026-11-02T00%3A00%3A00&date_max=2026-11-02T23%3A59%3A59"]


def test_requests_are_spaced(clock, tmp_path):
    times = []

    def handler(request):
        times.append(clock.now())
        return httpx.Response(200, json=[])

    _, client = make(clock, tmp_path, handler)
    client.bookings()
    client.bookings()
    assert (times[1] - times[0]).total_seconds() >= 1.5


@pytest.mark.parametrize("response", [
    httpx.Response(429, headers={"retry-after": "3600"}),
    httpx.Response(403),
    httpx.Response(200, html="<html>Access denied</html>"),
])
def test_block_signals_trip_breaker_and_lock(clock, tmp_path, response):
    calls = []

    def handler(request):
        calls.append(request)
        return response

    guard, client = make(clock, tmp_path, handler)
    with pytest.raises(Blocked):
        client.bookings()
    with pytest.raises(Blocked):
        client.bookings()
    assert len(calls) == 1
    assert (tmp_path / "blocked.json").exists()
    with pytest.raises(Blocked):
        guard.check_lock()


def test_lock_expires(clock, tmp_path):
    guard, client = make(clock, tmp_path, lambda r: httpx.Response(429), block_hours=1)
    with pytest.raises(Blocked):
        client.bookings()
    clock.sleep(3601)
    Guard(1.5, 30, tmp_path / "blocked.json", 1, now=clock.now).check_lock()
    assert not (tmp_path / "blocked.json").exists()


def test_server_error_retried_once(clock, tmp_path):
    responses = [httpx.Response(503), httpx.Response(200, json=[{"id": "x"}])]
    _, client = make(clock, tmp_path, lambda r: responses.pop(0))
    assert client.bookings() == [{"id": "x"}]


def test_server_error_not_retried_twice(clock, tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(503)

    _, client = make(clock, tmp_path, handler)
    with pytest.raises(ApiError):
        client.bookings()
    assert len(calls) == 2


def test_request_cap(clock, tmp_path):
    guard, client = make(clock, tmp_path, lambda r: httpx.Response(200, json=[]), max_requests=2)
    client.bookings()
    client.bookings()
    with pytest.raises(Blocked):
        client.bookings()
    assert guard.count == 2
