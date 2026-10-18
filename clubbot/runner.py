"""Un passage du cron : planning de J+14, attente de l'ouverture, réservations, vérification."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from itertools import groupby

from . import planner
from .client import ApiError, Blocked, ClubConnectClient, Guard
from .config import DAY_NAMES, Account, Course, Settings
from .notify import Notifier

log = logging.getLogger(__name__)

BOOKED = "réservé"
WAITING = "liste d'attente"
ALREADY = "déjà inscrit"
ABSENT = "absent du planning"
NOT_BOOKABLE = "non réservable"
FULL = "complet"
FAILED = "échec"
DRY_RUN = "simulation"
FAILURES = {FULL, FAILED}

_EPOCH = datetime.min.replace(tzinfo=timezone.utc)


@dataclass
class Outcome:
    account: str
    course: str
    status: str
    detail: str = ""

    def __str__(self) -> str:
        text = f"{self.account} · {self.course} : {self.status}"
        return f"{text} ({self.detail})" if self.detail else text


@dataclass
class Job:
    account: Account
    course: Course
    seance: dict
    waiting: bool = False


def _unique(accounts) -> list[Account]:
    return list({a.name: a for a in accounts}.values())


class Runner:
    def __init__(self, settings: Settings, accounts: list[Account], notifier: Notifier, *,
                 dry_run: bool = False, wait: bool = True, transport=None,
                 now=lambda: datetime.now(timezone.utc), sleep=time.sleep, clock=time.monotonic):
        self.settings = settings
        self.accounts = accounts
        self.notifier = notifier
        self.dry_run = dry_run
        self.wait = wait
        self._transport = transport
        self._now = now
        self._sleep = sleep
        self.guard = Guard(settings.min_interval_s, settings.max_requests, settings.lock_file,
                           settings.block_hours, clock=clock, sleep=sleep, now=now)
        self._clients: dict[str, ClubConnectClient] = {}
        self._login_errors: dict[str, str] = {}
        self._logins = 0
        self.outcomes: list[Outcome] = []
        self.blocked: str | None = None

    def run(self, target: date | None = None) -> list[Outcome]:
        if target is None:
            target = self._now().astimezone(self.settings.tz).date() + timedelta(days=self.settings.days_ahead)
        try:
            self.guard.check_lock()
            self._run(target)
        except Blocked as exc:
            self.blocked = str(exc)
            log.error("Arrêt de sécurité : %s", exc)
        finally:
            for client in self._clients.values():
                client.close()
        log.info("%d requête(s) envoyée(s) à clubconnect", self.guard.count)
        self._report(target)
        return self.outcomes

    def _add(self, account: Account, course: Course, status: str, detail: str = "") -> None:
        self.outcomes.append(Outcome(account.name, str(course), status, detail))

    def _client(self, account: Account) -> ClubConnectClient | None:
        if account.name in self._login_errors:
            return None
        if account.name in self._clients:
            return self._clients[account.name]
        if self._logins:
            self._sleep(self.settings.account_delay_s)
        self._logins += 1
        client = ClubConnectClient(self.settings.club_id, self.guard, label=account.name,
                                   transport=self._transport, sleep=self._sleep,
                                   clock_ms=lambda: int(self._now().timestamp() * 1000))
        try:
            client.login(account.email, account.password)
        except ApiError as exc:
            client.close()
            log.error("%s : connexion refusée (%s)", account.name, exc)
            self._login_errors[account.name] = f"connexion refusée : {exc}"
            return None
        log.info("%s : connecté", account.name)
        self._clients[account.name] = client
        return client

    def _run(self, target: date) -> None:
        tz = self.settings.tz
        weekday = target.weekday()
        wanted = [(a, c) for a in self.accounts for c in a.courses if c.weekday == weekday]
        log.info("Cible : %s %s, %d cours voulu(s)", DAY_NAMES[weekday], target, len(wanted))
        if not wanted:
            log.info("Rien à réserver, aucune requête envoyée")
            return

        planning = self._fetch_planning(target, _unique(a for a, _ in wanted))
        if planning is None:
            for account, course in wanted:
                self._add(account, course, FAILED, self._login_errors.get(account.name, "planning illisible"))
            return

        jobs = []
        for account, course in wanted:
            matches = planner.find_seances(course, planning, tz)
            if not matches:
                self._add(account, course, ABSENT, "pas de séance ce jour-là (férié ?)")
            elif len(matches) > 1:
                self._add(account, course, FAILED, f"{len(matches)} séances correspondent, préciser le nom du cours")
            elif reason := planner.block_reason(matches[0]):
                self._add(account, course, NOT_BOOKABLE, reason)
            else:
                jobs.append(Job(account, course, matches[0]))

        jobs = self._drop_already_booked(jobs)
        jobs.sort(key=lambda j: planner.opens_at(j.seance) or _EPOCH)
        posted = []
        for opening, group in groupby(jobs, key=lambda j: planner.opens_at(j.seance)):
            group = list(group)
            if problem := self._wait_for(opening):
                for job in group:
                    self._add(job.account, job.course, FAILED, problem)
                continue
            posted += self._book(group, target)
        self._verify(posted)

    def _fetch_planning(self, target: date, accounts: list[Account]) -> list[dict] | None:
        for account in accounts:
            client = self._client(account)
            if client is None:
                continue
            try:
                return client.seances(target)
            except ApiError as exc:
                log.error("planning illisible avec %s : %s", account.name, exc)
        return None

    def _drop_already_booked(self, jobs: list[Job]) -> list[Job]:
        remaining = []
        for account in _unique(j.account for j in jobs):
            mine = [j for j in jobs if j.account.name == account.name]
            client = self._client(account)
            if client is None:
                for job in mine:
                    self._add(job.account, job.course, FAILED, self._login_errors[account.name])
                continue
            try:
                bookings = client.bookings()
            except ApiError as exc:
                log.warning("%s : réservations illisibles (%s), on tente quand même", account.name, exc)
                bookings = []
            for job in mine:
                state = planner.booking_state(job.seance["id"], bookings)
                if state:
                    self._add(job.account, job.course, ALREADY, "en liste d'attente" if state == "waiting" else "")
                else:
                    remaining.append(job)
        return remaining

    def _wait_for(self, opening: datetime | None) -> str | None:
        """Attend l'ouverture des réservations ; renvoie un motif d'échec, ou None si on peut réserver."""
        if opening is None:
            return None
        s = self.settings
        local = opening.astimezone(s.tz)
        if local.strftime("%H:%M") != s.expected_open:
            log.warning("Ouverture annoncée le %s au lieu de %s", f"{local:%d/%m à %H:%M}", s.expected_open)
        fire = opening + timedelta(seconds=s.fire_offset_s)
        delay = (fire - self._now()).total_seconds()
        if delay <= 0:
            return None
        if delay > s.max_wait_min * 60:
            return f"ouverture le {local:%d/%m à %H:%M}, trop loin pour attendre"
        if not self.wait:
            if self.dry_run:
                return None
            return f"pas encore ouvert (ouverture à {local:%H:%M:%S}), relancer sans --no-wait"
        log.info("Attente de l'ouverture (%s + %ss), %.0f s", f"{local:%H:%M:%S}", s.fire_offset_s, delay)
        while (remaining := (fire - self._now()).total_seconds()) > 0:
            self._sleep(min(remaining, 30))
        return None

    def _book(self, group: list[Job], target: date) -> list[Job]:
        posted = []
        refreshed = None
        for job in group:
            client = self._client(job.account)
            if client is None:
                self._add(job.account, job.course, FAILED, self._login_errors[job.account.name])
                continue
            seance_id = job.seance["id"]
            if planner.is_full(job.seance):
                if not (self.settings.waitlist and planner.waitlist_open(job.seance)):
                    self._add(job.account, job.course, FULL)
                    continue
                job.waiting = True
            if self.dry_run:
                what = "une inscription en liste d'attente" if job.waiting else "une réservation"
                self._add(job.account, job.course, DRY_RUN, f"aurait envoyé {what} sur {seance_id}")
                continue
            try:
                client.book(seance_id, waiting=job.waiting)
            except ApiError as exc:
                log.warning("%s : réservation refusée : %s", job.account.name, exc)
                if job.waiting or not self.settings.waitlist:
                    self._add(job.account, job.course, FAILED, str(exc))
                    continue
                # Refus sans motif connu : on relit le planning (une fois pour le groupe) pour voir s'il est complet
                if refreshed is None:
                    try:
                        refreshed = client.seances(target)
                    except ApiError:
                        refreshed = []
                fresh = next((s for s in refreshed if s.get("id") == seance_id), None)
                if fresh is None or not planner.is_full(fresh):
                    self._add(job.account, job.course, FAILED, str(exc))
                    continue
                if not planner.waitlist_open(fresh):
                    self._add(job.account, job.course, FULL, "liste d'attente pleine")
                    continue
                job.waiting = True
                try:
                    client.book(seance_id, waiting=True)
                except ApiError as exc2:
                    self._add(job.account, job.course, FAILED, str(exc2))
                    continue
            posted.append(job)
        return posted

    def _verify(self, posted: list[Job]) -> None:
        for account in _unique(j.account for j in posted):
            mine = [j for j in posted if j.account.name == account.name]
            try:
                bookings = self._clients[account.name].bookings()
            except ApiError as exc:
                for job in mine:
                    self._add(job.account, job.course, FAILED, f"envoyé mais vérification impossible : {exc}")
                continue
            for job in mine:
                state = planner.booking_state(job.seance["id"], bookings)
                if state == "booked":
                    self._add(job.account, job.course, BOOKED)
                elif state == "waiting":
                    self._add(job.account, job.course, WAITING)
                else:
                    self._add(job.account, job.course, FAILED, "accepté par le site mais absent des réservations")

    def _report(self, target: date) -> None:
        for outcome in self.outcomes:
            log.info("%s", outcome)
        if not self.outcomes and not self.blocked:
            return
        lines = [str(o) for o in self.outcomes]
        if self.blocked:
            lines.insert(0, f"ARRÊT DE SÉCURITÉ : {self.blocked}")
            level = "error"
        elif any(o.status in FAILURES for o in self.outcomes):
            level = "warning"
        else:
            level = "info"
        self.notifier.send(f"ClubConnect {target:%d/%m}", "\n".join(lines), level)
