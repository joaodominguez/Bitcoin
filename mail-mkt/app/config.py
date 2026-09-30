from __future__ import annotations

import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent


def _database_url() -> str:
    return os.environ.get("DATABASE_URL") or f"sqlite:///{BASE_DIR / 'mailmkt.db'}"


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY") or "mail-mkt-dev-only"
    DATABASE_URL = _database_url()
    DASHBOARD_USER = os.environ.get("DASHBOARD_USER", "mailmkt")
    DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "")
    UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR") or (BASE_DIR / "uploads"))
    PAGE_SIZE = 50
    MAX_UPLOAD_MB = 50

    # UniOne
    UNIONE_API_KEY = os.environ.get("UNIONE_API_KEY", "")
    # Prefer api.unione.io; eu1/us1 also valid depending on account region
    UNIONE_BASE_URL = os.environ.get(
        "UNIONE_BASE_URL", "https://api.unione.io/en/transactional/api/v1"
    ).rstrip("/")
    UNIONE_FROM_EMAIL = os.environ.get("UNIONE_FROM_EMAIL", "")
    UNIONE_FROM_NAME = os.environ.get("UNIONE_FROM_NAME", "mail-mkt")
    UNIONE_REPLY_TO = os.environ.get("UNIONE_REPLY_TO", "")
    UNIONE_TRACK_LINKS = os.environ.get("UNIONE_TRACK_LINKS", "1") == "1"
    UNIONE_TRACK_READ = os.environ.get("UNIONE_TRACK_READ", "1") == "1"
    # Max recipients per email/send.json call (Unione hard limit = 500)
    UNIONE_BATCH_SIZE = min(500, max(1, int(os.environ.get("UNIONE_BATCH_SIZE", "100"))))
    UNIONE_BATCH_PAUSE_SEC = float(os.environ.get("UNIONE_BATCH_PAUSE_SEC", "1.0"))

    # Public URL for unsubscribe links + webhooks (no trailing slash)
    PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://91.99.167.243:8001").rstrip("/")
    WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "") or SECRET_KEY
