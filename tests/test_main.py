from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from clubbot.__main__ import next_run

PARIS = ZoneInfo("Europe/Paris")


def test_next_run_later_today():
    now = datetime(2026, 10, 19, 5, 0, tzinfo=timezone.utc)  # 07:00 Paris
    assert next_run(now, "07:50", PARIS) == datetime(2026, 10, 19, 7, 50, tzinfo=PARIS)


def test_next_run_tomorrow_across_dst_change():
    now = datetime(2026, 10, 24, 6, 0, tzinfo=timezone.utc)  # samedi 08:00 Paris, passage à l'heure d'hiver la nuit
    target = next_run(now, "07:50", PARIS)
    assert target.astimezone(timezone.utc) == datetime(2026, 10, 25, 6, 50, tzinfo=timezone.utc)
