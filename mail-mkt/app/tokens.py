from __future__ import annotations

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .config import Config


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(Config.SECRET_KEY, salt="mail-mkt-unsub")


def unsubscribe_token(email: str, contact_id: int | None = None) -> str:
    return _serializer().dumps({"email": email.lower(), "cid": contact_id})


def parse_unsubscribe_token(token: str, max_age: int = 60 * 60 * 24 * 365) -> dict | None:
    try:
        data = _serializer().loads(token, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None
    if not isinstance(data, dict) or not data.get("email"):
        return None
    return data
