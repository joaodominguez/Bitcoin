from __future__ import annotations

from functools import wraps

from flask import redirect, request, session, url_for

from .config import Config


def credentials_configured() -> bool:
    return bool(Config.DASHBOARD_PASSWORD)


def is_authenticated() -> bool:
    if not credentials_configured():
        return True
    if session.get("mailmkt_auth") is True:
        return True
    auth = request.authorization
    if (
        auth
        and auth.username == Config.DASHBOARD_USER
        and auth.password == Config.DASHBOARD_PASSWORD
    ):
        return True
    return False


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if is_authenticated():
            return view(*args, **kwargs)
        if request.path.startswith("/api/"):
            return {"error": "Autenticação necessária", "login": "/login"}, 401
        return redirect(url_for("login", next=request.path))

    return wrapped
