"""Testes de `admin/routes.py`.

Caminhos que não estavam cobertos: revogação de convite, reactivateção de
conta, e os casos de erro de cada um. São écritos agora porque uma rota de
administração sem teste é a rota onde um erro de autorização passa mais
tempo sem ser visto.
"""

from __future__ import annotations

import pytest
from asgi_client import SyncASGIClient
from conftest import csrf_from
from test_auth_flows import _signed_in

from mailutils.auth import service

EMAIL = "nova@exemplo.pt"


def _invite(app: SyncASGIClient, conn, admin_id: int, email: str = EMAIL) -> int:
    _token, _url = service.create_invite(conn, app.app.state.settings, email, admin_id)
    return service.list_invites(conn)[0]["id"]


def _post(app: SyncASGIClient, path: str, **data: object):
    token = csrf_from(app, "/admin")
    return app.post(path, data={"csrf_token": token, **data}, follow_redirects=True)


class TestInviteRevocation:
    def test_pending_invite_can_be_revoked(
        self, app: SyncASGIClient, conn, admin_user: int
    ) -> None:
        _signed_in(app, conn, admin_user)
        invite_id = _invite(app, conn, admin_user)
        response = _post(app, f"/admin/convites/{invite_id}/revogar")
        assert "revogado" in response.text.lower()
        assert service.list_invites(conn)[0]["revoked_at"] is not None

    def test_revoked_invite_cannot_be_accepted(
        self, app: SyncASGIClient, conn, admin_user: int
    ) -> None:
        token, _ = service.create_invite(conn, app.app.state.settings, EMAIL, admin_user)
        invite_id = service.list_invites(conn)[0]["id"]
        _signed_in(app, conn, admin_user)
        _post(app, f"/admin/convites/{invite_id}/revogar")

        app.clear_cookies()
        assert app.get(f"/convite/{token}").status_code == 410
        ok, _reason, _uid = service.accept_invite(conn, token, "uma-boa-palavra-1")
        assert ok is False

    def test_revoking_twice_is_harmless(self, app: SyncASGIClient, conn, admin_user: int) -> None:
        """O segundo revoke não tem a quem mudar. Devolver erro seria
        Bombardear o admin com um aviso que não significa nada."""
        _signed_in(app, conn, admin_user)
        invite_id = _invite(app, conn, admin_user)
        _post(app, f"/admin/convites/{invite_id}/revogar")
        response = _post(app, f"/admin/convites/{invite_id}/revogar")
        assert response.status_code == 200

    def test_unknown_invite_id_does_not_crash(
        self, app: SyncASGIClient, conn, admin_user: int
    ) -> None:
        _signed_in(app, conn, admin_user)
        response = _post(app, "/admin/convites/9999/revogar")
        assert response.status_code == 200

    def test_non_admin_cannot_revoke(
        self, app: SyncASGIClient, conn, admin_user: int, normal_user: int
    ) -> None:
        invite_id = _invite(app, conn, admin_user)
        _signed_in(app, conn, normal_user)
        response = _post(app, f"/admin/convites/{invite_id}/revogar")
        assert service.list_invites(conn)[0]["revoked_at"] is None
        assert response.status_code == 200

    def test_revocation_needs_a_csrf_token(
        self, app: SyncASGIClient, conn, admin_user: int
    ) -> None:
        invite_id = _invite(app, conn, admin_user)
        _signed_in(app, conn, admin_user)
        response = app.post(
            f"/admin/convites/{invite_id}/revogar",
            data={"csrf_token": "forjado"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert "erro=csrf" in response.headers["location"]
        assert service.list_invites(conn)[0]["revoked_at"] is None


class TestAccountState:
    def test_admin_can_deactivate(
        self, app: SyncASGIClient, conn, admin_user: int, normal_user: int
    ) -> None:
        _signed_in(app, conn, admin_user)
        response = _post(app, f"/admin/utilizadores/{normal_user}/estado")
        assert "Alterado" in response.text or "alterado" in response.text
        assert service.get_user(conn, normal_user)["is_active"] == 0

    def test_deactivated_user_cannot_sign_in(
        self, app: SyncASGIClient, conn, admin_user: int, normal_user: int
    ) -> None:
        from conftest import TEST_PASSWORD, UA_KNOWN, login

        _signed_in(app, conn, admin_user)
        _post(app, f"/admin/utilizadores/{normal_user}/estado")
        app.clear_cookies()
        assert "desactivada" in login(app, "ana@exemplo.pt", TEST_PASSWORD, UA_KNOWN).text

    def test_inactive_user_can_be_reactivated(
        self, app: SyncASGIClient, conn, admin_user: int, normal_user: int
    ) -> None:
        service.set_active(conn, normal_user, False)
        _signed_in(app, conn, admin_user)
        _post(app, f"/admin/utilizadores/{normal_user}/estado")
        assert service.get_user(conn, normal_user)["is_active"] == 1

    def test_deactivation_kills_live_sessions(
        self, app: SyncASGIClient, conn, admin_user: int, normal_user: int
    ) -> None:
        """Desactivar uma conta tem de cortar as sessões abertas nela, não só
        o login seguinte. Sem isto, um cookie válido continua a valer durante
        30 dias."""
        _signed_in(app, conn, normal_user)
        _signed_in(app, conn, admin_user)
        _post(app, f"/admin/utilizadores/{normal_user}/estado")
        assert (
            conn.execute(
                "SELECT COUNT(*) AS n FROM sessions WHERE user_id = ?", (normal_user,)
            ).fetchone()["n"]
            == 0
        )

    def test_unknown_user_id(self, app: SyncASGIClient, conn, admin_user: int) -> None:
        _signed_in(app, conn, admin_user)
        response = _post(app, "/admin/utilizadores/9999/estado")
        assert "inexistente" in response.text

    @pytest.mark.parametrize("action", ["estado", "revogar"])
    def test_state_change_needs_a_csrf_token(
        self, app: SyncASGIClient, conn, admin_user: int, normal_user: int, action: str
    ) -> None:
        _signed_in(app, conn, admin_user)
        before = conn.execute(
            "SELECT is_active FROM users WHERE id = ?", (normal_user,)
        ).fetchone()["is_active"]
        app.post(
            f"/admin/utilizadores/{normal_user}/{action}",
            data={"csrf_token": "forjado"},
            follow_redirects=False,
        )
        after = conn.execute("SELECT is_active FROM users WHERE id = ?", (normal_user,)).fetchone()[
            "is_active"
        ]
        assert before == after


class TestDashboardContent:
    def test_lists_users_and_invites(self, app: SyncASGIClient, conn, admin_user: int) -> None:
        _signed_in(app, conn, admin_user)
        _invite(app, conn, admin_user)
        body = app.get("/admin").text
        assert "admin@exemplo.pt" in body
        assert EMAIL in body
        assert "pendente" in body

    def test_shows_an_expired_invite_as_expired(
        self, app: SyncASGIClient, conn, admin_user: int
    ) -> None:
        _signed_in(app, conn, admin_user)
        invite_id = _invite(app, conn, admin_user)
        conn.execute(
            "UPDATE invites SET expires_at = '2020-01-01T00:00:00+00:00' WHERE id = ?",
            (invite_id,),
        )
        assert "expirado" in app.get("/admin").text

    def test_shows_an_accepted_invite(self, app: SyncASGIClient, conn, admin_user: int) -> None:
        _signed_in(app, conn, admin_user)
        invite_id = _invite(app, conn, admin_user)
        conn.execute(
            "UPDATE invites SET accepted_at = '2026-01-01T00:00:00+00:00' WHERE id = ?",
            (invite_id,),
        )
        assert "aceite" in app.get("/admin").text

    def test_second_invite_supersedes_the_first(
        self, app: SyncASGIClient, conn, admin_user: int
    ) -> None:
        """Criar um convite para um email que já tem um pendente substitui-o.

        A alternativa — dois tokens vivos — é pior do que parece: revogar um
        não revoga o outro, e a pessoa continua com um convite válido que o
        admin acredita ter cancelado. É também o caminho de reenvio.
        """
        _signed_in(app, conn, admin_user)
        first_token, _ = service.create_invite(conn, app.app.state.settings, EMAIL, admin_user)
        second_token, _ = service.create_invite(conn, app.app.state.settings, EMAIL, admin_user)
        assert first_token != second_token

        pending = [i for i in service.list_invites(conn) if not i["revoked_at"]]
        assert len(pending) == 1

        app.clear_cookies()
        assert app.get(f"/convite/{first_token}").status_code == 410
        assert app.get(f"/convite/{second_token}").status_code == 200

    def test_invite_for_an_existing_user_is_refused(
        self, app: SyncASGIClient, conn, admin_user: int
    ) -> None:
        _signed_in(app, conn, admin_user)
        response = _post(app, "/admin/convites", email="admin@exemplo.pt")
        assert "Já existe" in response.text
        assert service.list_invites(conn) == []

    def test_invalid_email_is_refused(self, app: SyncASGIClient, conn, admin_user: int) -> None:
        _signed_in(app, conn, admin_user)
        response = _post(app, "/admin/convites", email="nao-e-email")
        assert "inválido" in response.text
        assert service.list_invites(conn) == []

    def test_send_failure_is_reported_not_swallowed(
        self, app: SyncASGIClient, conn, admin_user: int, monkeypatch
    ) -> None:
        """Se o email não sai, dizer "enviado" é mentira. O utilizador ficaria
        à espera de um convite que nunca chega, e sem nenhum sinal de porquê."""

        def boom(*a: object, **k: object) -> None:
            raise RuntimeError("sem rede")

        monkeypatch.setattr("mailutils.auth.service.send_invite", boom)
        _signed_in(app, conn, admin_user)
        response = _post(app, "/admin/convites", email=EMAIL)
        assert "não saiu" in response.text
        assert "Convite enviado" not in response.text
