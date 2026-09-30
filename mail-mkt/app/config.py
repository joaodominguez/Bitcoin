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
