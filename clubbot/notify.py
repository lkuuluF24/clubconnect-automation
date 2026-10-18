"""Notifications de fin de passage."""
from __future__ import annotations

import logging

import httpx

log = logging.getLogger(__name__)

LEVELS = {"info": logging.INFO, "warning": logging.WARNING, "error": logging.ERROR}


class Notifier:
    def send(self, title: str, message: str, level: str = "info") -> None:
        raise NotImplementedError


class LogNotifier(Notifier):
    def send(self, title: str, message: str, level: str = "info") -> None:
        log.log(LEVELS[level], "%s\n%s", title, message)


class WebhookNotifier(Notifier):
    """POST JSON {title, message, level}, par exemple vers un webhook Home Assistant."""

    def __init__(self, url: str, transport: httpx.BaseTransport | None = None):
        self.url = url
        self._transport = transport

    def send(self, title: str, message: str, level: str = "info") -> None:
        try:
            with httpx.Client(transport=self._transport, timeout=10) as http:
                http.post(self.url, json={"title": title, "message": message, "level": level}).raise_for_status()
        except httpx.HTTPError as exc:
            log.error("notification non envoyée : %s", exc)
