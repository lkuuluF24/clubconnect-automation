import json

import httpx

from clubbot.notify import WebhookNotifier


def test_webhook_method_and_payload():
    seen = []

    def handler(request):
        seen.append((request.method, json.loads(request.content)))
        return httpx.Response(200)

    WebhookNotifier("https://ha.example/api/webhook/x", "PUT", transport=httpx.MockTransport(handler)).send(
        "ClubConnect 02/11", "AQUA POWER lundi 18:45 : réservé", "info", {"account": "luc"})
    assert seen == [("PUT", {"title": "ClubConnect 02/11", "message": "AQUA POWER lundi 18:45 : réservé",
                             "level": "info", "account": "luc"})]


def test_webhook_failure_does_not_raise():
    WebhookNotifier("https://ha.example/x", transport=httpx.MockTransport(lambda r: httpx.Response(500))).send("t", "m")
