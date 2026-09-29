"""Testes do prefixo de path e do URL público.

A aplicação é servida em `/xkmailutils`. Este ficheiro fixa três coisas:

1. **O URL da imagem é sempre `{host}/xkmailutils/media/{ficheiro}`**, seja qual
   for o host. É o requisito que o dono do projecto enunciou, e é o que entra
   no email de um destinatário que não conhece a instalação.
2. **Todas as rotas e ligações internas** respeitam o prefixo, incluindo
   cookies, redireccionamentos e o JavaScript.
3. **Servir na raiz continua a ser possível**, porque alguém vai querer. O que
   não pode acontecer é a app funcionar a um caminho e o email apontar para
   outro.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from asgi_client import SyncASGIClient
from conftest import TEST_PASSWORD, csrf_from
from test_auth_flows import _signed_in

from mailutils import config

PREFIXO = "/xkmailutils"


@pytest.fixture(autouse=True)
def env_limpo(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Isola o ambiente.

    Sem isto, um `.env` do developer altera o resultado destes testes na máquina
    de quem os corre e não no CI. E a validação do prefixo em modo produção
    precisa de um segredo válido, senão a recusa vem do segredo e não do
    prefixo — que é testar a coisa errada.
    """
    import os

    for key in list(os.environ):
        if key.startswith(("MAILUTILS_", "SMTP_")):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("MAILUTILS_SECRET_KEY", "k" * 48)
    monkeypatch.setenv("MAILUTILS_PUBLIC_BASE_URL", "https://x.pt")
    monkeypatch.setenv("MAILUTILS_DB_PATH", str(tmp_path / "p.db"))
    monkeypatch.setenv("MAILUTILS_MEDIA_DIR", str(tmp_path / "media"))


#: Hosts de teste. A lista é deliberadamente diverse: o requisito é "qualquer
#: host", e testar com um só seria testar com um.
HOSTS = (
    "https://mailutils.exemplo.pt",
    "http://192.168.1.50:8080",
    "https://xkmailutils.linuxkafe.pt",
    "https://sub.dominio.co.uk",
    "https://servidor-interno",
)


# ============================================================== configuração ==


class TestPrefixConfiguration:
    def test_default_prefix(self) -> None:
        assert config.DEFAULT_PATH_PREFIX == PREFIXO

    def test_default_is_used_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MAILUTILS_PATH_PREFIX", raising=False)
        assert config.load_settings().path_prefix == PREFIXO

    @pytest.mark.parametrize(
        ("raw", "esperado"),
        [
            ("/xkmailutils", "/xkmailutils"),
            ("xkmailutils", "/xkmailutils"),
            ("/xkmailutils/", "/xkmailutils"),
            ("//xkmailutils//", "/xkmailutils"),
            ("/a/b", "/a/b"),
            ("", ""),
            ("/", ""),
        ],
    )
    def test_normalisation(self, monkeypatch: pytest.MonkeyPatch, raw: str, esperado: str) -> None:
        """Barras a mais produziriam `//media/...`, que é um URL diferente em
        vários clientes de email — e uma imagem que não carrega."""
        monkeypatch.setenv("MAILUTILS_PATH_PREFIX", raw)
        assert config.load_settings().path_prefix == esperado

    @pytest.mark.parametrize(
        "raw", ["/xk mailutils", "/xk;mailutils", "/x?<>#", "/\\x", "http://x.pt/"]
    )
    def test_invalid_prefix_is_refused(self, monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
        """Um prefixo com espaços ou caracteres especiais é recusado no arranque.

        A consequência de o aceitar seria silenciosa e albuminada: o path
        teria de ser escapado em cada URL, e um espaço numa URL é um link que
        não abre no cliente de email.
        """
        monkeypatch.setenv("MAILUTILS_PATH_PREFIX", raw)
        with pytest.raises(config.ConfigError, match="PATH_PREFIX"):
            config.load_settings(env="production")

    def test_nested_prefix_is_allowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Alguém vai pôr a app em `/apps/mailutils`. Não proibir isso é mais
        útil do que impor um caminho e obrigar a um proxy."""
        monkeypatch.setenv("MAILUTILS_PATH_PREFIX", "/apps/mailutils")
        assert config.load_settings().path_prefix == "/apps/mailutils"


# =================================================================== URLs ==


class TestMediaUrlForAnyHost:
    """O requisito, tal como foi pedido: qualquer host, sempre com o prefixo."""

    @pytest.mark.parametrize("host", HOSTS)
    def test_media_url_shape(self, host: str) -> None:
        settings = config.load_settings(env="development")
        from dataclasses import replace

        settings = replace(settings, public_base_url=host, path_prefix=PREFIXO)
        assert settings.public_media_url("logo-7.png") == f"{host}{PREFIXO}/media/logo-7.png"

    @pytest.mark.parametrize("host", HOSTS)
    def test_media_url_has_exactly_one_slash_between_parts(self, host: str) -> None:
        from dataclasses import replace

        settings = replace(config.load_settings(), public_base_url=host + "/")
        url = settings.public_media_url("logo-7.png")
        assert "//media" not in url
        assert url.count("xkmailutils/media") == 1

    def test_prefix_is_never_dropped(self) -> None:
        """A falha que este teste existe para apanhar: alguém "simplifica" o
        construtor do URL e o prefixo desaparece. A imagem entra no email de
        um destinatuário que não conhece a instalação, e o sintoma é um logótipo
        que não aparece, sem erro em lado nenhum."""
        settings = config.load_settings()
        assert PREFIXO in settings.public_media_url("logo-1.png")

    def test_no_double_prefix(self) -> None:
        settings = config.load_settings()
        url = settings.public_media_url("logo-1.png")
        assert url.count("xkmailutils") == 1
        assert "xkmailutils/xkmailutils" not in url

    def test_base_url_carries_the_prefix(self) -> None:
        from dataclasses import replace

        settings = replace(config.load_settings(), public_base_url="https://x.pt")
        assert settings.base_url() == f"https://x.pt{PREFIXO}"

    def test_empty_prefix_still_works(self) -> None:
        """Servir na raiz é legítimo. O que não pode acontecer é a app funcionar
        a um caminho e o email apontar para outro."""
        from dataclasses import replace

        settings = replace(config.load_settings(), path_prefix="")
        assert settings.public_media_url("logo-1.png").endswith("/media/logo-1.png")
        assert settings.base_url() == settings.public_base_url

    def test_internal_urls_get_the_prefix(self) -> None:
        settings = config.load_settings()
        assert settings.url("/entrar") == f"{PREFIXO}/entrar"
        assert settings.url("entrar") == f"{PREFIXO}/entrar"


# ================================================================ routing ==


class TestRoutingUnderPrefix:
    def test_routes_are_registered_with_the_prefix(self, app: SyncASGIClient) -> None:
        caminhos = {r.path for r in app.app.routes if hasattr(r, "path")}
        for esperado in (
            f"{PREFIXO}/entrar",
            f"{PREFIXO}/verificar",
            f"{PREFIXO}/assinatura",
            f"{PREFIXO}/analisar",
            f"{PREFIXO}/admin",
            f"{PREFIXO}/media/{{filename}}",
            f"{PREFIXO}/saude",
        ):
            assert esperado in caminhos, f"falta a rota {esperado}"

    def test_login_page_is_reachable_through_the_prefix(self, app: SyncASGIClient) -> None:
        assert app.get("/entrar").status_code == 200

    def test_root_of_the_prefix_redirects_to_the_editor(self, app: SyncASGIClient) -> None:
        response = app.get("/", follow_redirects=False)
        assert response.status_code in (302, 303)
        assert response.headers["location"] == f"{PREFIXO}/assinatura"

    def test_static_is_under_the_prefix(self, app: SyncASGIClient) -> None:
        assert app.get("/static/app.css").status_code == 200

    def test_health_is_under_the_prefix(self, app: SyncASGIClient) -> None:
        assert app.get("/saude").status_code == 200

    def test_media_is_under_the_prefix(self, app: SyncASGIClient) -> None:
        assert app.get("/media/logo-999.png").status_code == 404


class TestLinksInThePage:
    def test_no_internal_link_lacks_the_prefix(self, app: SyncASGIClient) -> None:
        """Um `href="/entrar"` num template com a app em `/xkmailutils` é um
        404, e num `action` de formulário é um `POST` que não chega a lado
        nenhum — ou seja, um formulário que parece funcionar e não faz nada."""
        body = app.get("/entrar").text
        for atributo, valor in re.findall(r'(href|src|action)="([^"]*)"', body):
            if not valor.startswith("/"):
                continue
            assert valor.startswith(f"{PREFIXO}/") or valor == "/", (
                f'{atributo}="{valor}" não tem o prefixo'
            )

    def test_body_carries_the_prefix_for_javascript(self, app: SyncASGIClient) -> None:
        """O JavaScript descobre o prefixo por `data-raiz`, em vez de ter o
        caminho escrito em dois sítios que divergem."""
        assert f'data-raiz="{PREFIXO}"' in app.get("/entrar").text

    def test_static_link_in_template_is_prefixed(self) -> None:
        from pathlib import Path

        base = (
            Path(__file__).resolve().parent.parent / "src" / "mailutils" / "templates" / "base.html"
        ).read_text("utf-8")
        assert 'href="{{ raiz }}/static/app.css"' in base


class TestCookiesUnderPrefix:
    def test_session_cookie_path_is_the_prefix(
        self, app: SyncASGIClient, conn, normal_user: int
    ) -> None:
        """Com `path="/"`, o cookie de sessão seria enviado a cada outro
        serviço do mesmo host."""
        _signed_in(app, conn, normal_user)
        response = app.get("/perfil", follow_redirects=False)
        cookies = response.headers.get_list("set-cookie")
        # O middleware reescreve o cookie do tema em cada resposta, e ele é
        # posto com o path da aplicação. A sessão foi posta pelo atalho de
        # teste, sem passar pelo servidor, por isso o que se verifica aqui é
        # que *nenhum* cookie é emitido com `Path=/`.
        assert cookies
        for cookie in cookies:
            assert "Path=/," not in cookie and not cookie.rstrip().endswith("Path=/")
        assert any(f"Path={PREFIXO}/" in c for c in cookies)

    def test_session_cookie_set_by_the_server_uses_the_prefix(self, make_app) -> None:
        """Um login real tem de emitir o cookie de sessão com o path da
        aplicação. Com `path="/"`, seria enviado a todos os outros serviços do
        mesmo host — e o logout, que apaga pelo mesmo path, deixaria de
        funcionar.

        O dispositivo é registado à mão para o login ser directo: o caminho do
        segundo factor tem os seus próprios testes, e duplicá-lo aqui só
        tornaria este teste mais lento sem o tornar mais forte.
        """
        from conftest import csrf_from

        from mailutils import security
        from mailutils.auth import service

        agente = "Mozilla/5.0 Conhecido"
        client = make_app(path_prefix=PREFIXO)
        connection = client.app.state.db_factory()
        try:
            user_id = service.create_user(connection, "ana@exemplo.pt", TEST_PASSWORD)
            service.register_device(
                connection,
                client.app.state.settings,
                user_id,
                security.device_fingerprint(agente, ""),
                agente,
                "Conhecido",
            )
        finally:
            connection.close()

        client.get("/entrar")
        response = client.post(
            "/entrar",
            data={
                "email": "ana@exemplo.pt",
                "password": TEST_PASSWORD,
                "csrf_token": csrf_from(client),
            },
            headers={"user-agent": agente},
            follow_redirects=False,
        )
        cookies = response.headers.get_list("set-cookie")
        sessao = [c for c in cookies if "mailutils_session" in c]
        assert sessao, f"não foi emitido cookie de sessão: {cookies}"
        assert f"Path={PREFIXO}/" in sessao[0]

    def test_login_csrf_cookie_uses_the_prefix(self, app: SyncASGIClient) -> None:
        response = app.get("/entrar")
        cookies = response.headers.get_list("set-cookie")
        csrf = [c for c in cookies if "mailutils_login_csrf" in c]
        assert csrf
        assert f"Path={PREFIXO}/" in csrf[0]

    def test_challenge_cookie_path_is_prefixed(
        self, app: SyncASGIClient, conn, normal_user: int
    ) -> None:
        """A falha que quase passou: `path="/verificar"` com a app em
        `/xkmailutils` significa que o cookie nunca volta, e o sintoma é "o
        segundo factor não funciona" sem mensagem de erro nenhuma."""
        from conftest import login

        login(app, "ana@exemplo.pt", TEST_PASSWORD, "Mozilla/5.0 Novo")
        for cookie in app.cookie_jar().jar:
            if cookie.name == "mailutils_challenge":
                assert cookie.path in (f"{PREFIXO}/verificar", "/")
                return
        raise AssertionError("o cookie do desafio não foi emitido")


class TestRedirectsCarryThePrefix:
    def test_login_redirects_to_the_prefixed_verify_page(
        self, app: SyncASGIClient, conn, normal_user: int
    ) -> None:

        app.get("/entrar")
        token = csrf_from(app)
        response = app.post(
            "/entrar",
            data={
                "email": "ana@exemplo.pt",
                "password": TEST_PASSWORD,
                "csrf_token": token,
            },
            headers={"user-agent": "Mozilla/5.0 Novo"},
            follow_redirects=False,
        )
        assert response.headers["location"] == f"{PREFIXO}/verificar"

    def test_session_redirect_carries_the_prefix(self, app: SyncASGIClient) -> None:
        response = app.get("/assinatura", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"].startswith(f"{PREFIXO}/entrar")

    def test_invite_link_includes_the_prefix(
        self, app: SyncASGIClient, conn, admin_user: int
    ) -> None:
        """O link vai por email para um browser que não sabe nada desta
        instalação. Sem o prefixo, é um 404 — e o único sintoma é um convite
        que "não chega"."""
        from mailutils.auth import service

        _token, url = service.create_invite(
            conn, app.app.state.settings, "nova@exemplo.pt", admin_user
        )
        assert url.startswith(f"https://mailutils.exemplo.pt{PREFIXO}/convite/")


class TestMediaServing:
    def test_serves_the_logo_under_the_prefix(
        self, app: SyncASGIClient, conn, normal_user: int
    ) -> None:
        from test_editor_flows import _signed_in as sign_in
        from test_editor_flows import _upload

        sign_in(app, conn, normal_user)
        _upload(
            app,
            bytes.fromhex(
                "89504e470d0a1a0a0000000d494844520000000100000001080600000"
                "01f15c4890000000a49444154789c63000100000500010d0a2db400000000"
                "49454e44ae426082"
            ),
        )
        filename = conn.execute("SELECT filename FROM logos").fetchone()["filename"]
        response = app.get(f"/media/{filename}")
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"

    @pytest.mark.parametrize("name", ["../../../etc/passwd", "logo-1.png/../x", "nao-e-logo.png"])
    def test_traversal_still_refused_under_prefix(self, app: SyncASGIClient, name: str) -> None:
        assert app.get(f"/media/{name}").status_code in (400, 404)


# =============================================== servir na raiz (opcional) ==


class TestServingAtTheRoot:
    """Servir sem prefixo é legítimo.

    Estes testes existem para que o suporte não cresça por acidente, e para
    que quem o quebrar saiba que estava a funcionar de propósito.
    """

    def test_root_serving_works(self, tmp_path: Path) -> None:
        import dataclasses

        from mailutils.main import create_app

        settings = dataclasses.replace(
            config.load_settings(),
            db_path=tmp_path / "raiz.db",
            media_dir=tmp_path / "media",
            path_prefix="",
        )
        with SyncASGIClient(create_app(settings), base_url=settings.base_url()) as client:
            assert client.get("/entrar").status_code == 200
            assert client.get("/saude").status_code == 200
            assert client.get("/static/app.css").status_code == 200

    def test_media_url_has_no_prefix_when_disabled(self, tmp_path: Path) -> None:
        import dataclasses

        settings = dataclasses.replace(config.load_settings(), path_prefix="")
        assert settings.public_media_url("logo-1.png") == (
            f"{settings.public_base_url}/media/logo-1.png"
        )
