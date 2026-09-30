from __future__ import annotations

import os
from pathlib import Path

import pytest

# Isolate DB before any app import in test modules.
TMP_DB = Path("/tmp/mailmkt-pytest.db")
os.environ["DATABASE_URL"] = f"sqlite:///{TMP_DB}"
os.environ["SECRET_KEY"] = "test-secret"
os.environ.pop("DASHBOARD_PASSWORD", None)


@pytest.fixture(autouse=True)
def _clean_db():
    from app.db import Base, engine, init_db

    engine.dispose()
    Base.metadata.drop_all(bind=engine)
    init_db()
    yield
    engine.dispose()
    Base.metadata.drop_all(bind=engine)
