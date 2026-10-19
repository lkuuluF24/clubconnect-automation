"""Point d'entrée : python -m clubbot [--dry-run] [--date AAAA-MM-JJ] [--no-wait] [--every-day HH:MM]."""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from .config import DAY_NAMES, Account, ConfigError, Settings, _hhmm, load
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


def _host(url: str) -> str:
    # l'identifiant d'un webhook Home Assistant est un secret : on n'affiche que l'hôte
    return urlsplit(url).hostname or "?"


def describe(settings: Settings, accounts: list[Account], webhook: str | None) -> list[str]:
    """Ce que le service va faire, affiché au démarrage du conteneur."""
    lines = [f"Club {settings.club_id} : cours de J+{settings.days_ahead}, ouverture attendue à "
             f"{settings.expected_open} ({settings.tz.key}), liste d'attente {'oui' if settings.waitlist else 'non'}"]
    for account in accounts:
        notif = (f"notification {account.webhook_method} {_host(account.webhook)}"
                 if account.webhook else "pas de notification")
        lines.append(f"Compte {account.name} ({account.email}), {notif} :")
        for course in sorted(account.courses, key=lambda c: (c.weekday, c.time)):
            lines.append(f"  - {DAY_NAMES[course.weekday]} {course.time} {course.name or '(tout cours à cette heure)'}")
        if not account.courses:
            lines.append("  (aucun cours)")
    lines.append(f"Résumé global : {'webhook ' + _host(webhook) if webhook else 'journal uniquement'}")
    return lines


def upcoming(settings: Settings, accounts: list[Account], start: datetime) -> str:
    day = start.astimezone(settings.tz).date() + timedelta(days=settings.days_ahead)
    wanted = [f"{a.name} : {c.name or ''} {c.time}".replace("  ", " ")
              for a in accounts for c in a.courses if c.weekday == day.weekday()]
    head = f"cours du {DAY_NAMES[day.weekday()]} {day:%d/%m}"
    return f"{head} → {', '.join(wanted)}" if wanted else f"{head} → rien à réserver"


def every_day(args) -> int:
    """Mode service (Docker) : un passage par jour à l'heure donnée, heure du club."""
    try:
        for line in describe(*load(args.config, args.env)):
            log.info("%s", line)
    except ConfigError as exc:
        log.error("Configuration : %s", exc)
    while True:
        try:
            settings, accounts, _ = load(args.config, args.env)
        except ConfigError:
            settings, accounts = None, []
        tz = settings.tz if settings else ZoneInfo("Europe/Paris")
        target = next_run(datetime.now(timezone.utc), args.every_day, tz)
        plan = f", {upcoming(settings, accounts, target)}" if settings else ""
        log.info("Prochain passage le %s%s", f"{target:%d/%m/%Y à %H:%M %Z}", plan)
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
