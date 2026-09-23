import pytest

from btcsim.dashboard import app


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("DASHBOARD_PASSWORD", raising=False)
    monkeypatch.delenv("DASHBOARD_USER", raising=False)
    app.config["TESTING"] = True
    return app.test_client()


def test_health_is_public(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "ok"


def test_open_when_no_password(client):
    resp = client.get("/")
    assert resp.status_code == 200


def test_password_protects_pages(monkeypatch):
    monkeypatch.setenv("DASHBOARD_PASSWORD", "secret")
    monkeypatch.setenv("DASHBOARD_USER", "btcsim")
    client = app.test_client()
    assert client.get("/").status_code == 401
    assert client.get("/health").status_code == 200
    ok = client.get("/", auth=("btcsim", "secret"))
    assert ok.status_code == 200
    assert client.get("/", auth=("btcsim", "wrong")).status_code == 401
