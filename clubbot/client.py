"""Client de l'API members.clubconnect.fr, avec les garde-fous anti-blacklist.

Tous les comptes partent de la même IP : un seul `Guard` est partagé entre
tous les clients d'un passage (espacement global, plafond de requêtes,
coupe-circuit persistant sur disque).
"""
from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote

import httpx

from .signing import sign

log = logging.getLogger(__name__)

BASE_URL = "https://members.clubconnect.fr"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/141.0.0.0 Safari/537.36"
)
RETRYABLE = {500, 502, 503, 504}


class Blocked(Exception):
    """Le site nous bloque (ou risque de le faire) : on arrête tout le passage."""


class ApiError(Exception):
    def __init__(self, status: int | None, body: str, method: str, path: str):
        super().__init__(f"{method} {path} -> {status or 'réseau'} {body[:300]}")
        self.status = status
        self.body = body


class Guard:
    def __init__(self, min_interval_s: float, max_requests: int, lock_file: Path, block_hours: float,
                 *, clock=time.monotonic, sleep=time.sleep, now=lambda: datetime.now(timezone.utc)):
        self.min_interval_s = min_interval_s
        self.max_requests = max_requests
        self.lock_file = Path(lock_file)
        self.block_hours = block_hours
        self._clock = clock
        self._sleep = sleep
        self._now = now
        self._last: float | None = None
        self.count = 0
        self.tripped: str | None = None

    def check_lock(self) -> None:
        if not self.lock_file.exists():
            return
        data = json.loads(self.lock_file.read_text(encoding="utf-8"))
        until = datetime.fromisoformat(data["until"])
        if until > self._now():
            raise Blocked(f"verrou actif jusqu'au {until:%d/%m %H:%M} UTC ({data['reason']}), "
                          f"supprimer {self.lock_file} pour forcer")
        self.lock_file.unlink()

    def acquire(self) -> None:
        if self.tripped:
            raise Blocked(self.tripped)
        if self.count >= self.max_requests:
            self.tripped = f"plafond de {self.max_requests} requêtes atteint"
            raise Blocked(self.tripped)
        if self._last is not None:
            wait = self._last + self.min_interval_s - self._clock()
            if wait > 0:
                self._sleep(wait)
        self._last = self._clock()
        self.count += 1

    def trip(self, reason: str, retry_after_s: float | None = None):
        seconds = max(self.block_hours * 3600, retry_after_s or 0)
        until = self._now() + timedelta(seconds=seconds)
        self.lock_file.parent.mkdir(parents=True, exist_ok=True)
        self.lock_file.write_text(json.dumps({"until": until.isoformat(), "reason": reason}), encoding="utf-8")
        self.tripped = reason
        raise Blocked(reason)


def _retry_after(resp: httpx.Response) -> float | None:
    value = resp.headers.get("retry-after")
    if not value:
        return None
    if value.isdigit():
        return float(value)
    try:
        return (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
    except (TypeError, ValueError):
        return None


class ClubConnectClient:
    def __init__(self, club_id: str, guard: Guard, *, label: str, transport: httpx.BaseTransport | None = None,
                 timeout: float = 15.0, retry_delay_s: float = 3.0, sleep=time.sleep,
                 clock_ms=lambda: int(time.time() * 1000)):
        self.club_id = club_id
        self.guard = guard
        self.label = label
        self._retry_delay_s = retry_delay_s
        self._sleep = sleep
        self._clock_ms = clock_ms
        self._http = httpx.Client(
            base_url=BASE_URL,
            transport=transport,
            timeout=timeout,
            headers={
                "accept": "application/json, text/plain, */*",
                "accept-language": "fr-FR,fr;q=0.9",
                "content-type": "application/json",
                "app-version": "undefined",
                "with-cookie": "true",
                "version-check-bypass": "true",
                "platform": "members",
                "origin": BASE_URL,
                "referer": f"{BASE_URL}/club/{club_id}/planning",
                "user-agent": USER_AGENT,
            },
        )

    def close(self) -> None:
        self._http.close()

    def _request(self, method: str, path: str, *, query: str = "", body: dict | None = None):
        url = f"{path}?{query}" if query else path
        content = json.dumps(body) if body is not None else None
        for attempt in (1, 2):
            self.guard.acquire()
            timestamp = str(self._clock_ms())
            headers = {"x-timestamp": timestamp, "x-signature": sign(method, path, timestamp)}
            try:
                resp = self._http.request(method, url, headers=headers, content=content)
            except httpx.TransportError as exc:
                if attempt == 1:
                    log.warning("%s : %s %s, erreur réseau (%s), un seul nouvel essai", self.label, method, path, exc)
                    self._sleep(self._retry_delay_s)
                    continue
                raise ApiError(None, str(exc), method, path) from exc
            if resp.status_code in RETRYABLE and attempt == 1:
                log.warning("%s : %s %s -> %d, un seul nouvel essai", self.label, method, path, resp.status_code)
                self._sleep(self._retry_delay_s)
                continue
            return self._handle(resp, method, path)

    def _handle(self, resp: httpx.Response, method: str, path: str):
        status = resp.status_code
        log.debug("%s : %s %s -> %d", self.label, method, path, status)
        if status in (403, 429):
            self.guard.trip(f"{method} {path} -> HTTP {status}", _retry_after(resp))
        if "html" in resp.headers.get("content-type", ""):
            self.guard.trip(f"{method} {path} -> page HTML inattendue (HTTP {status})", _retry_after(resp))
        if status >= 400:
            raise ApiError(status, resp.text, method, path)
        if status in (201, 204) or not resp.content:
            return None
        try:
            return resp.json()
        except ValueError:
            self.guard.trip(f"{method} {path} -> réponse non JSON (HTTP {status})")

    def login(self, email: str, password: str) -> None:
        self._request("POST", "/api/mobile/session", body={"email": email, "password": password})
        if not self._http.cookies:
            log.warning("%s : aucun cookie de session reçu", self.label)

    def seances(self, day: date) -> list[dict]:
        query = "&".join([
            f"clubs={self.club_id}",
            f"date_min={quote(f'{day}T00:00:00', safe='')}",
            f"date_max={quote(f'{day}T23:59:59', safe='')}",
        ])
        return self._request("GET", "/api/mobile/seance", query=query) or []

    def bookings(self) -> list[dict]:
        return self._request("GET", "/api/mobile/booking") or []

    def book(self, seance_id: str, *, waiting: bool) -> None:
        self._request("POST", "/api/mobile/booking",
                      body={"seance": {"id": seance_id, "ccClubId": self.club_id}, "waiting": waiting})
