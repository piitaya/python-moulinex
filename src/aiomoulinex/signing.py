"""AWS Signature Version 4 for the MQTT connection to AWS IoT."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import hmac
from typing import TYPE_CHECKING
from urllib.parse import quote

if TYPE_CHECKING:
    from .models import AwsCredentials

_ALGORITHM = "AWS4-HMAC-SHA256"
_SERVICE = "iotdata"
_PATH = "/mqtt"


def _hmac(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode(), hashlib.sha256).digest()


def signing_key(secret_key: str, date: str, region: str, service: str) -> bytes:
    """Return the key that signs the requests of one day to one service."""
    key = _hmac(f"AWS4{secret_key}".encode(), date)
    for part in (region, service, "aws4_request"):
        key = _hmac(key, part)
    return key


def signed_mqtt_path(
    credentials: AwsCredentials,
    host: str,
    region: str,
    now: datetime | None = None,
) -> str:
    """Return the signed path and query of the MQTT websocket of AWS IoT."""
    now = now or datetime.now(UTC)
    timestamp = now.strftime("%Y%m%dT%H%M%SZ")
    date = now.strftime("%Y%m%d")
    scope = f"{date}/{region}/{_SERVICE}/aws4_request"
    query = "&".join(
        f"{key}={quote(value, safe='')}"
        for key, value in (
            ("X-Amz-Algorithm", _ALGORITHM),
            ("X-Amz-Credential", f"{credentials.access_key}/{scope}"),
            ("X-Amz-Date", timestamp),
            ("X-Amz-SignedHeaders", "host"),
        )
    )
    request = "\n".join(
        ("GET", _PATH, query, f"host:{host}\n", "host", hashlib.sha256(b"").hexdigest())
    )
    to_sign = "\n".join(
        (_ALGORITHM, timestamp, scope, hashlib.sha256(request.encode()).hexdigest())
    )
    key = signing_key(credentials.secret_key, date, region, _SERVICE)
    signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    # AWS IoT wants the session token outside of what is signed.
    token = quote(credentials.session_token, safe="")
    return f"{_PATH}?{query}&X-Amz-Signature={signature}&X-Amz-Security-Token={token}"
