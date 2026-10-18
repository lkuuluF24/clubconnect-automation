"""Chargement de config.yaml (cours, réglages) et de .env (identifiants)."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

DAYS = {"lundi": 0, "mardi": 1, "mercredi": 2, "jeudi": 3, "vendredi": 4, "samedi": 5, "dimanche": 6}
DAY_NAMES = {v: k for k, v in DAYS.items()}


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Course:
    weekday: int
    time: str  # "HH:MM", heure locale du club
    name: str | None = None

    def __str__(self) -> str:
        when = f"{DAY_NAMES[self.weekday]} {self.time}"
        return f"{self.name} {when}" if self.name else when


@dataclass
class Account:
    name: str
    email: str
    password: str = field(repr=False)
    courses: list[Course] = field(default_factory=list)
    webhook: str | None = field(default=None, repr=False)
    webhook_method: str = "POST"


@dataclass
class Settings:
    club_id: str
    lock_file: Path
    tz: ZoneInfo = ZoneInfo("Europe/Paris")
    days_ahead: int = 14
    expected_open: str = "08:00"
    max_wait_min: float = 120
    fire_offset_s: float = 1.0
    waitlist: bool = True
    min_interval_s: float = 1.5
    account_delay_s: float = 5.0
    max_requests: int = 30
    block_hours: float = 24


def _hhmm(value) -> str:
    # YAML 1.1 lit 18:45 sans guillemets comme un entier sexagésimal (1125)
    if isinstance(value, int):
        value = f"{value // 60}:{value % 60:02d}"
    try:
        return datetime.strptime(str(value).strip(), "%H:%M").strftime("%H:%M")
    except ValueError:
        raise ConfigError(f"heure invalide : {value!r} (attendu HH:MM)") from None


def _course(raw) -> Course:
    if not isinstance(raw, dict):
        raise ConfigError(f"cours invalide : {raw!r}")
    day = str(raw.get("day", "")).strip().lower()
    if day not in DAYS:
        raise ConfigError(f"jour inconnu : {day!r} (lundi … dimanche)")
    name = str(raw["name"]).strip() if raw.get("name") else None
    return Course(DAYS[day], _hhmm(raw.get("time")), name)


def read_env_file(path: Path | None) -> dict[str, str]:
    values: dict[str, str] = {}
    if path is None or not Path(path).exists():
        return values
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key] = value
    return values


def load(config_path: Path, env_path: Path | None) -> tuple[Settings, list[Account], str | None]:
    config_path = Path(config_path)
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        raise ConfigError(f"{config_path} introuvable") from None
    env = {**read_env_file(env_path), **os.environ}

    if not raw.get("club_id"):
        raise ConfigError("club_id manquant")
    safety = raw.get("safety") or {}
    lock_file = Path(safety.get("lock_file", "state/blocked.json"))
    if not lock_file.is_absolute():
        lock_file = config_path.parent / lock_file
    settings = Settings(
        club_id=str(raw["club_id"]),
        lock_file=lock_file,
        tz=ZoneInfo(raw.get("timezone", "Europe/Paris")),
        days_ahead=int(raw.get("days_ahead", 14)),
        expected_open=_hhmm(raw.get("expected_open", "08:00")),
        max_wait_min=float(raw.get("max_wait_minutes", 120)),
        fire_offset_s=float(raw.get("fire_offset_seconds", 1)),
        waitlist=bool(raw.get("waitlist", True)),
        min_interval_s=float(safety.get("min_interval_seconds", 1.5)),
        account_delay_s=float(safety.get("account_delay_seconds", 5)),
        max_requests=int(safety.get("max_requests_per_run", 30)),
        block_hours=float(safety.get("block_hours", 24)),
    )
    if settings.min_interval_s < 1:
        raise ConfigError("safety.min_interval_seconds doit être ≥ 1 (rate limit du site)")

    common = [_course(c) for c in raw.get("common_courses") or []]
    accounts_raw = raw.get("accounts") or {}
    if not accounts_raw:
        raise ConfigError("aucun compte dans accounts")
    accounts = []
    for name, acc in accounts_raw.items():
        acc = acc or {}
        if not re.fullmatch(r"[A-Za-z0-9_]+", str(name)):
            raise ConfigError(f"nom de compte invalide : {name!r} (lettres, chiffres, _)")
        key = f"ACCOUNT_{str(name).upper()}"
        email, password = env.get(f"{key}_EMAIL"), env.get(f"{key}_PASSWORD")
        if not email or not password:
            raise ConfigError(f"{key}_EMAIL / {key}_PASSWORD manquants dans .env")
        method = (env.get(f"{key}_WEBHOOK_METHOD") or "POST").upper()
        if method not in ("POST", "PUT"):
            raise ConfigError(f"{key}_WEBHOOK_METHOD doit valoir POST ou PUT")
        extra = [_course(c) for c in acc.get("extra_courses") or []]
        accounts.append(Account(str(name), email, password, list(dict.fromkeys(common + extra)),
                                env.get(f"{key}_WEBHOOK") or None, method))
    return settings, accounts, env.get("NOTIFY_WEBHOOK_URL") or None
