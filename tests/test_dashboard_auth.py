import pytest

from btcsim.dashboard import app


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("DASHBOARD_PASSWORD", raising=False)
    monkeypatch.delenv("DASHBOARD_USER", raising=False)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret"
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
    assert 'id="w_top_cards"' in html
    assert 'id="w_perf_chart"' in html
    assert "Desempenho da carteira" in html
    assert "Watchlist" in html
    assert "Últimas transações" in html or "Movimentos recentes" in html
    assert "Histórico de movimentos" in html or 'data-view="movements"' in html
    assert "cdn.tailwindcss.com" in html
    assert "Ouro" in html and "Metais" in html
    assert "Petróleo" in html and "Energia" in html
    assert "Tecnologia" in html
    assert "fetchJson" in html
    assert 'id="app_status"' in html
    icon = client.get("/static/favicon.png")
    assert icon.status_code == 200
    assert icon.mimetype == "image/png"
    assert icon.data[:8] == b"\x89PNG\r\n\x1a\n"


def test_portfolio_page_refreshes_itself(client):
    html = client.get("/").get_data(as_text=True)
    assert "WATCH_REFRESH_MS = 30000" in html
    assert "setInterval" in html
    assert "30s" in html


def test_password_redirects_to_login(monkeypatch):
    monkeypatch.setenv("DASHBOARD_PASSWORD", "secret")
    monkeypatch.setenv("DASHBOARD_USER", "btcsim")
    app.config["SECRET_KEY"] = "test-secret"
    client = app.test_client()
    resp = client.get("/")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]

    login_page = client.get("/login")
    assert login_page.status_code == 200
    body = login_page.get_data(as_text=True)
    assert "Carteira virtual" in body
    assert 'name="password"' in body

    api = client.get("/api/watch")
    assert api.status_code == 401
    assert api.get_json()["login"] == "/login"

    bad = client.post("/login", data={"username": "btcsim", "password": "wrong"})
    assert bad.status_code == 200
    assert "incorretos" in bad.get_data(as_text=True)

    ok = client.post(
        "/login",
        data={"username": "btcsim", "password": "secret"},
        follow_redirects=False,
    )
    assert ok.status_code == 302
    home = client.get("/")
    assert home.status_code == 200
    assert 'id="w_top_cards"' in home.get_data(as_text=True)

    watch = client.get("/api/watch")
    assert watch.status_code == 200

    # Basic Auth still works without a cookie session
    other = app.test_client()
    basic = other.get("/", auth=("btcsim", "secret"))
    assert basic.status_code == 200
    assert other.get("/", auth=("btcsim", "wrong")).status_code == 302
