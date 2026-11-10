from sqlmodel import text

from app.db import get_session


def test_home_shows_empty_applications_page(client):
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "<h1>Applications</h1>" in response.text


def test_unknown_page_returns_404(client):
    assert client.get("/does-not-exist").status_code == 404


def test_get_session_can_query_the_database():
    sessions = get_session()
    session = next(sessions)
    assert session.exec(text("SELECT 1")).one() == (1,)
    sessions.close()  # runs the end of the `with` block in get_session -> session closed
