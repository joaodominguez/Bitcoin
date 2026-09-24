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


def test_favicon_and_asset_areas(client):
    page = client.get("/")
    html = page.get_data(as_text=True)
    assert page.status_code == 200
    assert "favicon.png" in html
    assert "capital-rail" in html
    assert "stage" in html
    assert "Ouro" in html and "Metais" in html
    assert "Petróleo" in html and "Energia" in html
    assert "Tecnologia" in html
    icon = client.get("/static/favicon.png")
    assert icon.status_code == 200
    assert icon.mimetype == "image/png"
    assert icon.data[:8] == b"\x89PNG\r\n\x1a\n"


def test_portfolio_page_refreshes_itself(client):
    html = client.get("/").get_data(as_text=True)
    assert "WATCH_REFRESH_MS = 30000" in html
    assert "setInterval" in html
    assert "atualiza a cada 30 s" in html


def test_password_protects_pages(monkeypatch):
    monkeypatch.setenv("DASHBOARD_PASSWORD", "secret")
    monkeypatch.setenv("DASHBOARD_USER", "btcsim")
    client = app.test_client()
    assert client.get("/").status_code == 401
    assert client.get("/health").status_code == 200
    ok = client.get("/", auth=("btcsim", "secret"))
    assert ok.status_code == 200
    assert client.get("/", auth=("btcsim", "wrong")).status_code == 401
