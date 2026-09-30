from __future__ import annotations

from datetime import timedelta

from flask import Flask

from .config import Config
from .db import init_db


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["SECRET_KEY"] = Config.SECRET_KEY
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)
    app.config["MAX_CONTENT_LENGTH"] = Config.MAX_UPLOAD_MB * 1024 * 1024

    init_db()

    from . import routes  # noqa: WPS433

    app.register_blueprint(routes.bp)
    return app
