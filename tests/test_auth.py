import pytest
from fastapi.testclient import TestClient

from app.config import Settings


def login(client: TestClient, settings: Settings, next_url: str = "/"):
    return client.post(
        "/login",
        data={
            "username": settings.app_user,
            "password": settings.app_password,
            "next": next_url,
        },
        follow_redirects=False,
    )


def test_protected_page_redirects_to_login(client: TestClient) -> None:
    response = client.get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=%2F"


def test_wrong_password_does_not_create_session(client: TestClient, settings: Settings) -> None:
    response = client.post(
        "/login",
        data={"username": settings.app_user, "password": "wrong", "next": "/"},
        follow_redirects=False,
    )

    assert response.status_code == 401
    assert "Неверный логин или пароль" in response.text
    assert "session" not in client.cookies


def test_login_creates_session_and_opens_summary(client: TestClient, settings: Settings) -> None:
    response = login(client, settings)

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert "session" in client.cookies

    summary = client.get("/")
    assert summary.status_code == 200
    assert "Сводка" in summary.text


def test_logout_clears_session(client: TestClient, settings: Settings) -> None:
    login(client, settings)

    response = client.post("/logout", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    assert "session" not in client.cookies
    assert client.get("/", follow_redirects=False).status_code == 303


@pytest.mark.parametrize("next_url", ["https://example.com", "//example.com", "/\\example.com"])
def test_external_next_is_ignored(client: TestClient, settings: Settings, next_url: str) -> None:
    response = login(client, settings, next_url=next_url)

    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_login_page_redirects_authenticated_user(client: TestClient, settings: Settings) -> None:
    login(client, settings)

    response = client.get("/login", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/"
