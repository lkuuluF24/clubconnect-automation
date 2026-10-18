"""Signature des requêtes, reproduite depuis main.48f7cb33.js (fonction `Go`).

x-signature = HMAC-SHA256("MÉTHODE:chemin:timestamp_ms") en hexadécimal,
chemin sans la query string. La clé est stockée dans le JS XORée avec 90.
"""
import hashlib
import hmac

_OBFUSCATED_KEY = bytes([
    53, 53, 52, 59, 51, 52, 104, 59, 47, 11, 47, 105, 53, 50, 62, 98, 59, 50, 61, 50,
    53, 53, 25, 51, 63, 41, 63, 51, 62, 59, 50, 54, 63, 63, 41, 63, 51, 54, 59, 50,
    104, 59, 61, 50, 98, 61, 59, 50, 108, 53, 52, 61, 108, 42, 59, 62, 110, 53, 53, 52,
])
_KEY = bytes(b ^ 90 for b in _OBFUSCATED_KEY)


def sign(method: str, path: str, timestamp_ms: str) -> str:
    message = f"{method.upper()}:{path}:{timestamp_ms}".encode()
    return hmac.new(_KEY, message, hashlib.sha256).hexdigest()
