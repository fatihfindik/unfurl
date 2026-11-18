"""Settings › Browser extension (D-140): the working extension sign-ins and "Revoke"."""

import json

from app.services import token_service
from app.services.auth_service import create_user
from tests.helpers import csrf_token


def htmx(client) -> dict:
    return {"HX-Request": "true", "X-CSRF-Token": csrf_token(client)}


def revoke(client, token_id):
    return client.post(f"/settings/extension/tokens/{token_id}/revoke", headers=htmx(client))


def test_the_page_lists_the_working_sign_ins(auth_client, session, user):
    _, working = token_service.issue(session, user)
    _, revoked = token_service.issue(session, user)
    token_service.revoke(session, revoked)

    html = auth_client.get("/settings").text

    assert "Browser extension" in html
    assert f"/settings/extension/tokens/{working.id}/revoke" in html
    assert f"/settings/extension/tokens/{revoked.id}/revoke" not in html
    assert "not used yet" in html
    assert "Load unpacked" in html


def test_without_sign_ins_the_page_says_so(auth_client):
    assert "No devices connected." in auth_client.get("/settings").text


def test_revoke_signs_the_extension_out(auth_client, session, user):
    raw, token = token_service.issue(session, user)

    response = revoke(auth_client, token.id)

    assert response.status_code == 200
    assert json.loads(response.headers["HX-Trigger"])["toast"]["message"] == "Extension signed out."
    assert "No devices connected." in response.text
    session.expire_all()  # the route changed the token in its own session
    assert token_service.check(session, raw) is None
    # The web session stays.
    assert auth_client.get("/settings", follow_redirects=False).status_code == 200


def test_another_users_token_is_a_404(auth_client, session):
    other = create_user(session, email="other@example.com", password="x" * 12, full_name="O")
    raw, token = token_service.issue(session, other)

    assert revoke(auth_client, token.id).status_code == 404
    assert token_service.check(session, raw) is not None


def test_unknown_and_odd_ids_are_a_404(auth_client):
    for token_id in ("999", "0", "-1", "abc", str(2**40)):
        assert revoke(auth_client, token_id).status_code == 404, token_id


def test_revoke_needs_the_csrf_token(auth_client, session, user):
    raw, token = token_service.issue(session, user)

    response = auth_client.post(
        f"/settings/extension/tokens/{token.id}/revoke", headers={"HX-Request": "true"}
    )

    assert response.status_code == 403
    assert token_service.check(session, raw) is not None


def test_revoke_needs_a_login(client, session, user):
    raw, token = token_service.issue(session, user)

    response = client.post(
        f"/settings/extension/tokens/{token.id}/revoke",
        data={"csrf_token": csrf_token(client)},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")
    assert token_service.check(session, raw) is not None


def test_the_privacy_page_mentions_the_extension(client):
    html = client.get("/privacy").text
    assert "Browser extension" in html
    assert "never reads your cookies" in html
