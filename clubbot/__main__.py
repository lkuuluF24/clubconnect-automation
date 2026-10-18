"""Point d'entrée : python -m clubbot [--dry-run] [--date AAAA-MM-JJ] [--no-wait] [--every-day HH:MM]."""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import ConfigError, _hhmm, load
from .notify import LogNotifier, WebhookNotifier
from .runner import FAILURES, Runner

log = logging.getLogger("clubbot")


def _at(value: str) -> str:
    try:
        return _hhmm(value)
    except ConfigError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def next_run(now: datetime, at: str, tz: ZoneInfo) -> datetime:
    hour, minute = map(int, at.split(":"))
    local = now.astimezone(tz)
    candidate = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= local:
        candidate = (local + timedelta(days=1)).replace(hour=hour, minute=minute, second=0, microsecond=0)
    return candidate


def every_day(args) -> int:
    """Mode service (Docker) : un passage par jour à l'heure donnée, heure du club."""
    while True:
        try:
            tz = load(args.config, args.env)[0].tz
        except ConfigError:
            tz = ZoneInfo("Europe/Paris")
        target = next_run(datetime.now(timezone.utc), args.every_day, tz)
        log.info("Prochain passage le %s", f"{target:%d/%m/%Y à %H:%M %Z}")
        while (remaining := (target - datetime.now(timezone.utc)).total_seconds()) > 0:
            time.sleep(min(remaining, 60))
        code = run_once(args)
        log.info("Passage terminé (code %d)", code)


def run_once(args) -> int:
    try:
        settings, accounts, webhook = load(args.config, args.env)
    except ConfigError as exc:
        log.error("Configuration : %s", exc)
        return 3

    notifier = WebhookNotifier(webhook) if webhook else LogNotifier()
    runner = Runner(settings, accounts, notifier, dry_run=args.dry_run, wait=not args.no_wait)
    outcomes = runner.run(args.date)
    if runner.blocked:
        return 2
    return 1 if any(o.status in FAILURES for o in outcomes) else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="clubbot", description="Réserve les cours favoris à J+14.")
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    parser.add_argument("--env", type=Path, default=Path(".env"))
    parser.add_argument("--dry-run", action="store_true", help="tout faire sauf envoyer les réservations")
    parser.add_argument("--date", type=date.fromisoformat, help="jour visé (défaut : aujourd'hui + days_ahead)")
    parser.add_argument("--no-wait", action="store_true", help="ne pas attendre l'heure d'ouverture")
    parser.add_argument("--every-day", metavar="HH:MM", type=_at,
                        help="rester actif et lancer un passage chaque jour à cette heure (heure du club)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if args.every_day:
        return every_day(args)
    return run_once(args)


if __name__ == "__main__":
    sys.exit(main())
