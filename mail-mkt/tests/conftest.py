from __future__ import annotations

import os
from pathlib import Path

# Isolate DB before any app import in test modules.
TMP_DB = Path("/tmp/mailmkt-pytest.db")
os.environ["DATABASE_URL"] = f"sqlite:///{TMP_DB}"
os.environ["SECRET_KEY"] = "test-secret"
os.environ.pop("DASHBOARD_PASSWORD", None)
