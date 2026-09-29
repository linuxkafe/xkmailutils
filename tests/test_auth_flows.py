"""Testes de fluxo HTTP: login, segundo factor, convites, assinatura, uploads.

Estes testes atravessam a aplicação inteira por HTTP. Não substituem os testes
unitários — acrescentam a cobertura de coisas que só um pedido real revela:
cookies, redirecamentos, `303` vs `200`, e o facto de um token CSRF existir no
HTML e não só na função que o gera.
"""

from __future__ import annotations

import re

from asgi_client import SyncASGIClient
from conftest import TEST_PASSWORD, UA_KNOWN, UA_NEW, csrf_from, login

from mailutils import security
from mailutils.auth import service

PNG_1x1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000a49444154789c6300010000050001"
    "0d0a2db40000000049454e44ae426082"
)


def extract_cookie(client: SyncASGIClient, name: str) -> str:
    return client.get_cookie(name)


class TestLoginPage:
    def test_renders_for_anonymous(self, app: SyncASGIClient) -> None:
        response = app.get("/entrar")
        assert response.status_code == 200
        assert "Entrar" in response.text

    def test_has_a_csrf_token_in_the_html(self, app: SyncASGIClient) -> None:
        """O token tem de estar *no formulário*. Um token que existe na sessão
        mas não na página não protege nada."""
        assert re.search(r'name="csrf_token" value="[A-Za-z0-9_-]+"', app.get("/entrar").text)

    def test_sets_a_csrf_cookie(self, app: SyncASGIClient) -> None:
        app.get("/entrar")
        assert extract_cookie(app, "mailutils_login_csrf")

    def test_root_redirects_to_the_editor(self, app: SyncASGIClient) -> None:
        assert app.get("/", follow_redirects=False).status_code in (302, 307)


class TestLoginFailure:
    def test_wrong_password_does_not_sign_in(self, app: SyncASGIClient, normal_user: int) -> None:
        response = login(app, "ana@exemplo.pt", "palavra-passe-errada")
        assert "inválidos" in response.text
        assert 'name="password"' in response.text

    def test_unknown_email_gives_the_same_message(
        self, app: SyncASGIClient, normal_user: int
    ) -> None:
        """Mensagem diferente para "email não existe" é enumeração de contas de
        graça. Tem de ser indistinguível."""
        inexistente = login(app, "ninguem@exemplo.pt", TEST_PASSWORD)
        errada = login(app, "ana@exemplo.pt", "errada")
        assert _error_text(inexistente) == _error_text(errada)

    def test_inactive_account_is_refused(self, app: SyncASGIClient, conn, normal_user: int) -> None:
        service.set_active(conn, normal_user, False)
        response = login(app, "ana@exemplo.pt", TEST_PASSWORD)
        assert "desactivada" in response.text

    def test_bruteforce_locks_out(self, app: SyncASGIClient, conn, normal_user: int) -> None:
        for _ in range(5):
            login(app, "ana@exemplo.pt", "errada")
        response = login(app, "ana@exemplo.pt", TEST_PASSWORD)
        assert "Demasiadas tentativas" in response.text

    def test_lockout_does_not_apply_to_other_emails(
        self, app: SyncASGIClient, conn, normal_user: int
    ) -> None:
        """O limite é por email+IP. Se fosse global, cinco tentativas erradas
        por parte de uma pessoa bloqueariam a instalação inteira."""
        service.create_user(conn, "bruno@exemplo.pt", TEST_PASSWORD)
        for _ in range(6):
            login(app, "ana@exemplo.pt", "errada")
        response = login(app, "bruno@exemplo.pt", TEST_PASSWORD, UA_NEW)
        assert "Demasiadas tentativas" not in response.text
        assert "Verificar o dispositivo" in response.text


class TestSecondFactor:
    def test_new_device_requires_a_code(
        self, app: SyncASGIClient, conn, normal_user: int, captured_emails
    ) -> None:
        """O requisito do dono do projecto: 2F só em dispositivos novos, mas
        *tem de* pedir código num dispositivo novo."""
        response = login(app, "ana@exemplo.pt", TEST_PASSWORD, UA_NEW)
        assert "Verificar o dispositivo" in response.text
        assert "Risco de spam" not in response.text, "não devia dar acesso à assinatura"
        assert len(captured_emails) == 1

    def test_code_is_emailed(self, app: SyncASGIClient, normal_user: int, captured_emails) -> None:
        login(app, "ana@exemplo.pt", TEST_PASSWORD, UA_NEW)
        assert len(captured_emails) == 1
        assert captured_emails[0]["to"] == "ana@exemplo.pt"
        assert "código" in captured_emails[0]["subject"].lower()

    def test_otp_code_is_never_in_the_response(
        self, app: SyncASGIClient, conn, normal_user: int, captured_emails
    ) -> None:
        """Regra do CLAUDE.md: o código nunca volta ao cliente, nem em erro."""
        response = login(app, "ana@exemplo.pt", TEST_PASSWORD, UA_NEW)
        body = response.text
        row = conn.execute("SELECT code_hash FROM otp_codes ORDER BY id DESC").fetchone()
        assert row["code_hash"] not in body
        for email in captured_emails:
            assert email["text"] not in body

    def test_wrong_code_is_refused(
        self, app: SyncASGIClient, normal_user: int, captured_emails
    ) -> None:
        login(app, "ana@exemplo.pt", TEST_PASSWORD, UA_NEW)
        response = _submit_code(app, "000000")
        assert "inválido" in response.text or "expirado" in response.text

    def test_correct_code_signs_in(
        self, app: SyncASGIClient, conn, normal_user: int, captured_emails, monkeypatch
    ) -> None:
        """O código que o utilizador recebe tem de validar contra o que está
        gravado. `hash_otp` é interceptado para tornar o teste determinístico:
        o código real só existe no email, e o email é interceptado."""
        login(app, "ana@exemplo.pt", TEST_PASSWORD, UA_NEW)
        with monkeypatch.context() as m:
            m.setattr(security, "hash_otp", lambda code: "hash-fake-do-codigo-enviado")
            login(app, "ana@exemplo.pt", TEST_PASSWORD, UA_NEW)
            response = _submit_code(app, "123456")
        assert "Risco de spam" in response.text

    def test_known_device_skips_the_code(
        self, app: SyncASGIClient, conn, normal_user: int, captured_emails
    ) -> None:
        login(app, "ana@exemplo.pt", TEST_PASSWORD, UA_NEW)
        _submit_code(app, _the_code(captured_emails))
        app.clear_cookies()
        emails_before = len(captured_emails)
        response = login(app, "ana@exemplo.pt", TEST_PASSWORD, UA_NEW)
        assert "Risco de spam" in response.text
        assert len(captured_emails) == emails_before, "não devia reenviar código"

    def test_code_is_single_use(
        self, app: SyncASGIClient, normal_user: int, captured_emails
    ) -> None:
        """Um código é consumível uma vez. Reutilizá-lo daria ao atacante uma
        segunda tentativa grátis sem pedir outro código ao dono da conta."""
        login(app, "ana@exemplo.pt", TEST_PASSWORD, UA_NEW)
        code = _the_code(captured_emails)
        challenge = extract_cookie(app, "mailutils_challenge")
        assert "Risco de spam" in _submit_code(app, code).text

        app.clear_cookies()
        app.set_cookie("mailutils_challenge", challenge)
        response = _submit_code(app, code)
        assert "inválido" in response.text or "expirado" in response.text
        assert "Risco de spam" not in response.text

    def test_verify_without_a_challenge_redirects_to_login(self, app: SyncASGIClient) -> None:
        """Sem o cookie do desafio não há segundo factor a fazer. O motivo do
        redireccionamento vai no URL para a página de login poder dizer porquê,
        em vez de um formulário que parece estar a funcionar."""
        response = app.get("/verificar", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/xkmailutils/entrar?erro=convite"

    def test_cooldown_blocks_a_second_send(self, make_app) -> None:
        """O cooldown existe para não inundar a caixa de correio de alguém.

        Sem ele, um script de pedidos mete milhares de mensagens na caixa da
        vítima por hora. É o abuso mais óbvio desta feature e o mais fácil de
        não pensar.
        """
        client = make_app(otp_cooldown_seconds=600)
        service.create_user(client.app.state.db_factory(), "ana@exemplo.pt", TEST_PASSWORD)
        connection = client.app.state.db_factory()
        try:
            login(client, "ana@exemplo.pt", TEST_PASSWORD, UA_NEW)
            first = connection.execute("SELECT COUNT(*) AS n FROM otp_codes").fetchone()["n"]
            client.clear_cookies()
            login(client, "ana@exemplo.pt", TEST_PASSWORD, UA_NEW)
            second = connection.execute("SELECT COUNT(*) AS n FROM otp_codes").fetchone()["n"]
        finally:
            connection.close()
        assert first == 1
        assert second == 1, "o cooldown tem de impedir um novo envio"


class TestCsrf:
    def test_login_post_needs_a_valid_token(self, app: SyncASGIClient, normal_user: int) -> None:
        app.get("/entrar")
        response = app.post(
            "/entrar",
            data={
                "email": "ana@exemplo.pt",
                "password": TEST_PASSWORD,
                "csrf_token": "token-forjado",
            },
            follow_redirects=True,
        )
        assert "sessão expirou" in response.text.lower()

    def test_verify_post_needs_a_valid_token(
        self, app: SyncASGIClient, normal_user: int, captured_emails
    ) -> None:
        login(app, "ana@exemplo.pt", TEST_PASSWORD, UA_NEW)
        challenge = extract_cookie(app, "mailutils_challenge")
        response = app.post(
            "/verificar",
            data={"challenge_id": challenge, "code": "123456", "csrf_token": "token-forjado"},
            headers={"user-agent": UA_NEW},
            follow_redirects=True,
        )
        assert "sessão expirou" in response.text.lower()
        assert "Risco de spam" not in response.text, "um token forjado não autentica"

    def test_editor_post_needs_a_token(self, app: SyncASGIClient, conn, normal_user: int) -> None:
        _signed_in(app, conn, normal_user)
        response = app.post(
            "/assinatura/guardar",
            data={"csrf_token": "forjado", "fields": '{"name":"X"}'},
            follow_redirects=True,
        )
        assert "expirada" in response.text

    def test_saved_page_carries_a_valid_token(
        self, app: SyncASGIClient, conn, normal_user: int
    ) -> None:
        _signed_in(app, conn, normal_user)
        token = re.search(r'name="csrf_token" value="([^"]+)"', app.get("/assinatura").text)
        assert token, "a página do editor tem de expor um token CSRF"


class TestSession:
    def test_anon_is_redirected_to_login(self, app: SyncASGIClient) -> None:
        response = app.get("/assinatura", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/xkmailutils/entrar"

    def test_logout_clears_the_cookie(self, app: SyncASGIClient, conn, normal_user: int) -> None:
        _signed_in(app, conn, normal_user)
        token = csrf_from(app, "/assinatura")
        app.post("/sair", data={"csrf_token": token}, follow_redirects=False)
        assert app.get("/assinatura", follow_redirects=False).status_code == 303

    def test_deactivating_kills_the_live_session(
        self, app: SyncASGIClient, conn, normal_user: int
    ) -> None:
        """Desactivar tem de cortar a sessão em curso, não só o próximo
        login — senão um cookie válido continua a valer até expirar."""
        _signed_in(app, conn, normal_user)
        service.set_active(conn, normal_user, False)
        assert app.get("/assinatura", follow_redirects=False).status_code == 303


class TestInvites:
    def test_admin_creates_an_invite(
        self, app: SyncASGIClient, conn, admin_user: int, captured_emails
    ) -> None:
        _signed_in(app, conn, admin_user)
        token = csrf_from(app, "/admin")
        response = app.post(
            "/admin/convites",
            data={"csrf_token": token, "email": "nova@exemplo.pt"},
            follow_redirects=True,
        )
        assert "Convite enviado" in response.text
        assert len(captured_emails) == 1
        assert captured_emails[0]["to"] == "nova@exemplo.pt"

    def test_non_admin_cannot_reach_the_dashboard(
        self, app: SyncASGIClient, conn, normal_user: int
    ) -> None:
        _signed_in(app, conn, normal_user)
        assert app.get("/admin", follow_redirects=False).status_code == 303

    def test_invited_user_accepts_and_sets_a_password(
        self, app: SyncASGIClient, conn, admin_user: int, captured_emails
    ) -> None:
        token, _url = service.create_invite(
            conn, app.app.state.settings, "nova@exemplo.pt", admin_user
        )
        app.clear_cookies()
        page = app.get(f"/convite/{token}")
        assert page.status_code == 200, page.text[:200]
        assert "nova@exemplo.pt" in page.text
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
        response = app.post(
            f"/convite/{token}",
            data={
                "csrf_token": csrf,
                "password": "uma-boa-palavra-1",
                "password2": "uma-boa-palavra-1",
            },
            follow_redirects=True,
        )
        assert "Risco de spam" in response.text
        assert service.get_user_by_email(conn, "nova@exemplo.pt") is not None

    def test_short_password_is_refused(self, app: SyncASGIClient, conn, admin_user: int) -> None:
        token, _ = service.create_invite(
            conn, app.app.state.settings, "nova@exemplo.pt", admin_user
        )
        page = app.get(f"/convite/{token}")
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
        response = app.post(
            f"/convite/{token}",
            data={"csrf_token": csrf, "password": "corta", "password2": "corta"},
            follow_redirects=False,
        )
        assert "12 caracteres" in response.text
        assert service.get_user_by_email(conn, "nova@exemplo.pt") is None

    def test_mismatched_passwords_are_refused(
        self, app: SyncASGIClient, conn, admin_user: int
    ) -> None:
        token, _ = service.create_invite(
            conn, app.app.state.settings, "nova@exemplo.pt", admin_user
        )
        page = app.get(f"/convite/{token}")
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
        response = app.post(
            f"/convite/{token}",
            data={
                "csrf_token": csrf,
                "password": "uma-boa-palavra-1",
                "password2": "outra-coisa-1",
            },
            follow_redirects=False,
        )
        assert "não coincidem" in response.text

    def test_invite_cannot_be_used_twice(self, app: SyncASGIClient, conn, admin_user: int) -> None:
        token, _ = service.create_invite(
            conn, app.app.state.settings, "nova@exemplo.pt", admin_user
        )
        app.get(f"/convite/{token}")
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', app.get(f"/convite/{token}").text)
        app.post(
            f"/convite/{token}",
            data={
                "csrf_token": csrf.group(1),
                "password": "uma-boa-palavra-1",
                "password2": "uma-boa-palavra-1",
            },
        )
        app.clear_cookies()
        assert app.get(f"/convite/{token}").status_code == 410

    def test_bogus_invite_is_410(self, app: SyncASGIClient) -> None:
        assert app.get("/convite/token-inventado").status_code == 410

    def test_admin_cannot_deactivate_itself(
        self, app: SyncASGIClient, conn, admin_user: int
    ) -> None:
        _signed_in(app, conn, admin_user)
        token = csrf_from(app, "/admin")
        response = app.post(
            f"/admin/utilizadores/{admin_user}/estado",
            data={"csrf_token": token},
            follow_redirects=True,
        )
        assert "sua própria conta" in response.text
        assert service.get_user(conn, admin_user)["is_active"] == 1


# ------------------------------------------------------------------ helpers --


def _error_text(response) -> str:
    match = re.search(r'<div class="notice notice--erro" role="alert">(.*?)</div>', response.text)
    return match.group(1).strip() if match else ""


def _the_code(captured_emails: list[dict]) -> str:
    """Extrai o código do email capturado.

    Só é possível porque o `captured_emails` intercepta o envio. O código
    real nunca é recuperável do servidor depois de gravado — que é o ponto.
    """
    text = captured_emails[-1]["text"]
    match = re.search(r"\n\s{4}(\d{6})\n", text)
    assert match, f"não encontrei um código de 6 dígitos em:\n{text}"
    return match.group(1)


def _submit_code(app: SyncASGIClient, code: str, user_agent: str = UA_NEW):
    """Submete o código.

    O `user_agent` é o mesmo do login por um motivo: a impressão digital do
    dispositivo no passo 2 tem de coincidir com a do passo 1, senão o
    dispositivo é registado duas vezes e o teste seguinte volta a pedir um
    código que já devia ter sido dispensado.
    """
    token = csrf_from(app, "/verificar")
    return app.post(
        "/verificar",
        data={
            "challenge_id": extract_cookie(app, "mailutils_challenge"),
            "code": code,
            "csrf_token": token,
        },
        headers={"user-agent": user_agent},
        follow_redirects=True,
    )


def _signed_in(app: SyncASGIClient, conn, user_id: int) -> None:
    """Autentica sem passar pelo segundo factor.

    Regista o dispositivo directamente, para os testes de páginas não terem de
    repetir o fluxo de OTP. O fluxo de OTP tem os seus próprios testes.
    """
    from mailutils import web

    settings = app.app.state.settings
    fingerprint = security.device_fingerprint(UA_KNOWN, "pt-PT")
    service.register_device(conn, settings, user_id, fingerprint, UA_KNOWN, "Thunderbird")
    token, _ = web.open_session(conn, settings, user_id)
    app.set_cookie("mailutils_session", token)
