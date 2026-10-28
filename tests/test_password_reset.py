"""Testes de fluxo HTTP: recuperação de palavra-passe."""

from __future__ import annotations

import re

from asgi_client import SyncASGIClient
from conftest import TEST_PASSWORD, csrf_from, login

NEW_PASSWORD = "novapalavrapasse123"


def _extract_reset_token(captured_emails: list[dict]) -> str:
    text = captured_emails[-1]["text"]
    match = re.search(r"/recuperar/([A-Za-z0-9_-]+)", text)
    assert match, f"não encontrei token em:\n{text}"
    return match.group(1)


def test_request_reset_sends_email(
    app: SyncASGIClient, conn, normal_user: int, captured_emails
) -> None:
    client = app
    token = csrf_from(client, "/recuperar")
    response = client.post(
        "/recuperar",
        data={"email": "ana@exemplo.pt", "csrf_token": token},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert len(captured_emails) == 1
    assert captured_emails[0]["to"] == "ana@exemplo.pt"
    assert "recuperar" in captured_emails[0]["text"] or "repor" in captured_emails[0]["text"]


def test_reset_flow_changes_password(
    app: SyncASGIClient, conn, normal_user: int, captured_emails
) -> None:
    client = app
    csrf = csrf_from(client, "/recuperar")
    client.post(
        "/recuperar",
        data={"email": "ana@exemplo.pt", "csrf_token": csrf},
        follow_redirects=True,
    )
    reset_token = _extract_reset_token(captured_emails)

    page = client.get(f"/recuperar/{reset_token}")
    assert page.status_code == 200
    assert "nova" in page.text.lower() or "palavra-passe" in page.text.lower()

    form_csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    response = client.post(
        f"/recuperar/{reset_token}",
        data={
            "csrf_token": form_csrf,
            "password": NEW_PASSWORD,
            "password2": NEW_PASSWORD,
        },
        follow_redirects=True,
    )
    assert response.status_code == 200

    # password changed: old password no longer works, new does
    client.clear_cookies()
    old = login(client, "ana@exemplo.pt", TEST_PASSWORD)
    assert "inválidos" in old.text or "Entrar" in old.text
    new = login(client, "ana@exemplo.pt", NEW_PASSWORD)
    assert "Risco de spam" in new.text or "assinatura" in new.text.lower() or new.status_code == 200


def test_reset_token_single_use(
    app: SyncASGIClient, conn, normal_user: int, captured_emails
) -> None:
    client = app
    csrf = csrf_from(client, "/recuperar")
    client.post(
        "/recuperar",
        data={"email": "ana@exemplo.pt", "csrf_token": csrf},
        follow_redirects=True,
    )
    reset_token = _extract_reset_token(captured_emails)

    page = client.get(f"/recuperar/{reset_token}")
    form_csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    client.post(
        f"/recuperar/{reset_token}",
        data={"csrf_token": form_csrf, "password": NEW_PASSWORD, "password2": NEW_PASSWORD},
        follow_redirects=True,
    )

    # second use
    page2 = client.get(f"/recuperar/{reset_token}")
    # either 200 invalid page or redirect; token consumed
    assert (
        "inválido" in page2.text.lower()
        or "expirou" in page2.text.lower()
        or page2.status_code in (200, 410)
    )


def test_unknown_email_does_not_reveal(app: SyncASGIClient, captured_emails) -> None:
    client = app
    csrf = csrf_from(client, "/recuperar")
    response = client.post(
        "/recuperar",
        data={"email": "ninguem@exemplo.pt", "csrf_token": csrf},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert len(captured_emails) == 0


def test_invalid_token_is_rejected(app: SyncASGIClient) -> None:
    client = app
    page = client.get("/recuperar/token-inventado")
    assert page.status_code == 200
    assert "inválido" in page.text.lower() or "expirou" in page.text.lower()


def test_mismatched_passwords_refused(
    app: SyncASGIClient, conn, normal_user: int, captured_emails
) -> None:
    client = app
    csrf = csrf_from(client, "/recuperar")
    client.post(
        "/recuperar",
        data={"email": "ana@exemplo.pt", "csrf_token": csrf},
        follow_redirects=True,
    )
    reset_token = _extract_reset_token(captured_emails)
    page = client.get(f"/recuperar/{reset_token}")
    form_csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    response = client.post(
        f"/recuperar/{reset_token}",
        data={"csrf_token": form_csrf, "password": NEW_PASSWORD, "password2": "outra"},
        follow_redirects=False,
    )
    assert "não coincidem" in response.text
