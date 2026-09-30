from __future__ import annotations

import io
import os
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.db import Base, SessionLocal, engine, init_db
from app.factory import create_app
from app.import_csv import import_contacts, normalize_email
from app.models import Contact, ContactList, Suppression

TMP_DB = Path("/tmp/mailmkt-pytest.db")


@pytest.fixture(autouse=True)
def _clean_db():
    engine.dispose()
    Base.metadata.drop_all(bind=engine)
    init_db()
    yield
    engine.dispose()
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def test_normalize_email():
    assert normalize_email("  Foo@Bar.COM ") == "foo@bar.com"
    assert normalize_email("not-an-email") is None
    assert normalize_email("") is None


def test_import_csv_dedupe_and_list():
    sample = Path(__file__).resolve().parents[1] / "examples" / "sample_contacts.csv"
    with SessionLocal() as session:
        with sample.open("rb") as fh:
            result = import_contacts(session, fh, list_name="demo", default_source="csv")
        session.commit()
        assert result.created == 5
        assert result.skipped_invalid == 1
        assert result.skipped_duplicate_in_file == 1
        assert session.scalar(select(func.count()).select_from(Contact)) == 5
        assert session.scalar(
            select(func.count()).select_from(ContactList).where(ContactList.name == "demo")
        ) == 1


def test_import_skips_suppressed():
    with SessionLocal() as session:
        session.add(Suppression(email="ana.silva@example.com", reason="unsubscribe"))
        session.commit()
        csv_data = b"email,nome\nana.silva@example.com,Ana\noutro@example.com,Outro\n"
        result = import_contacts(session, io.BytesIO(csv_data))
        session.commit()
        assert result.skipped_suppressed == 1
        assert result.created == 1
        emails = set(session.scalars(select(Contact.email)).all())
        assert emails == {"outro@example.com"}


def test_health_and_contacts_page(client):
    assert client.get("/health").status_code == 200
    assert client.get("/health").get_json()["app"] == "mail-mkt"
    page = client.get("/contacts")
    assert page.status_code == 200
    assert b"Contactos" in page.data
    assert b"mail-" in page.data


def test_import_via_http(client):
    sample = Path(__file__).resolve().parents[1] / "examples" / "sample_contacts.csv"
    data = {
        "list_name": "http-demo",
        "source": "csv",
        "update_existing": "1",
        "file": (sample.open("rb"), "sample_contacts.csv"),
    }
    resp = client.post(
        "/import", data=data, content_type="multipart/form-data", follow_redirects=True
    )
    assert resp.status_code == 200
    assert b"novos" in resp.data or b"Import conclu" in resp.data
    contacts = client.get("/contacts")
    assert b"ana.silva@example.com" in contacts.data


def test_password_protects():
    from app import config as config_mod

    config_mod.Config.DASHBOARD_PASSWORD = "secret"
    config_mod.Config.DASHBOARD_USER = "mailmkt"
    try:
        app = create_app()
        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "test-secret"
        c = app.test_client()
        resp = c.get("/contacts")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]
        login = c.post("/login", data={"username": "mailmkt", "password": "secret"})
        assert login.status_code == 302
        assert c.get("/contacts").status_code == 200
    finally:
        config_mod.Config.DASHBOARD_PASSWORD = ""
        os.environ.pop("DASHBOARD_PASSWORD", None)
