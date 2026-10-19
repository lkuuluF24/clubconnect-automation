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


def _setup(tmp_path):
    from clubbot.config import Account, Course, Settings
    settings = Settings(club_id="club", lock_file=tmp_path / "blocked.json")
    accounts = [
        Account("luc", "luc@example.org", "pw", [Course(3, "18:45", "AQUA POWER"), Course(0, "18:45", "AQUA POWER")],
                "https://ha.example.org/api/webhook/SECRET"),
        Account("marie", "marie@example.org", "pw", [Course(1, "19:00", "AQUA POWER")]),
    ]
    return settings, accounts


def test_describe_lists_courses_and_hides_webhook_id(tmp_path):
    from clubbot.__main__ import describe
    lines = describe(*_setup(tmp_path), None)
    text = "\n".join(lines)
    assert "Compte luc (luc@example.org), notification POST ha.example.org :" in lines
    assert lines.index("  - lundi 18:45 AQUA POWER") < lines.index("  - jeudi 18:45 AQUA POWER")
    assert "Compte marie (marie@example.org), pas de notification :" in lines
    assert "SECRET" not in text and "pw" not in text
    assert lines[-1] == "Résumé global : journal uniquement"


def test_upcoming(tmp_path):
    from clubbot.__main__ import upcoming
    settings, accounts = _setup(tmp_path)
    start = datetime(2026, 10, 19, 7, 50, tzinfo=PARIS)
    assert upcoming(settings, accounts, start) == "cours du lundi 02/11 → luc : AQUA POWER 18:45"
    start = datetime(2026, 10, 21, 7, 50, tzinfo=PARIS)
    assert upcoming(settings, accounts, start) == "cours du mercredi 04/11 → rien à réserver"
