"""Fixtures partilhadas.

Duas decisões:

1. **Cada teste recebe uma aplicação nova com uma base de dados em memória.**
   Nenhum estado atravessa testes. Um teste que depende da ordem é um teste que
   falha sozinho na CI, três semanas depois.

2. **O backend de email é `null` por omissão.** Nenhum teste pode enviar email
   a ninguém. Os testes que precisam de saber se um email saiu usam o
   `captured_emails` em vez de SMTP.
"""

from __future__ import annotations

import dataclasses
import re
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from asgi_client import SyncASGIClient

from mailutils import config, db
from mailutils.auth import service
from mailutils.main import create_app

#: Senha de teste. Não é um segredo: vive no repositório, num ficheiro que só
#: o CI lê. Está aqui para ser óbvia, não para ser adivinhada.
TEST_PASSWORD = "correcthorsebattery1"


@pytest.fixture
def captured_emails(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """Captura os emails em vez de os enviar.

    Intercepts `mailutils.mailer.send`, que é o único ponto por onde passa um
    email — tanto o OTP como o convite. Um teste que verifique "foi enviado um
    código" está assim a testar o comportamento, não o SMTP.
    """
    sent: list[dict] = []

    def fake_send(settings, to_address, subject, text, html=None):  # noqa: ANN001
        sent.append(
            {
                "to": to_address,
                "subject": subject,
                "text": text,
                "html": html,
                "settings": settings,
            }
        )

    monkeypatch.setattr("mailutils.mailer.send", fake_send)
    return sent


@pytest.fixture
def settings(tmp_path: Path) -> config.Settings:
    """Configuração de teste: determinística, isolada, sem rede."""
    base = config.load_settings(env="development")
    return dataclasses.replace(
        base,
        secret_key="chave-de-teste-nao-usar-em-producao-1234567890",
        db_path=tmp_path / "test.db",
        media_dir=tmp_path / "media",
        public_base_url="https://mailutils.exemplo.pt",
        path_prefix="/xkmailutils",
        https=True,
        secure_cookies=False,
        mail_backend="null",
        admin_email="",
        admin_password="",
        otp_ttl_minutes=10,
        otp_cooldown_seconds=0,
        login_max_attempts=5,
        login_window_minutes=15,
    )


@pytest.fixture
def make_app():
    """Fábrica de aplicações com `settings` alterados.

    `Settings` é um dataclass *frozen*: mutá-lo a meio de um teste seria uma
    mutação global que o teste seguinte herda. Substituir a aplicação
    inteira é mais caro e é o correcto.
    """
    created: list[SyncASGIClient] = []

    def _make(**overrides) -> SyncASGIClient:
        base = config.load_settings(env="development")
        import dataclasses
        import tempfile
        from pathlib import Path

        tmp = Path(tempfile.mkdtemp(prefix="mailutils-test-"))
        defaults = {
            "secret_key": "chave-de-teste-nao-usar-em-producao-1234567890",
            "db_path": tmp / "test.db",
            "media_dir": tmp / "media",
            "public_base_url": "https://mailutils.exemplo.pt",
            "path_prefix": "/xkmailutils",
            "secure_cookies": False,
            "mail_backend": "null",
            "admin_email": "",
            "admin_password": "",
            "otp_cooldown_seconds": 0,
        }
        resolved = dataclasses.replace(base, **{**defaults, **overrides})
        client = SyncASGIClient(create_app(resolved), base_url=resolved.base_url())
        created.append(client)
        return client

    yield _make
    for client in created:
        client.close()


@pytest.fixture
def app(settings: config.Settings) -> Iterator[SyncASGIClient]:
    """Aplicação com o lifespan corrido (migrações aplicadas).

    Entra pelo cliente como context manager para que o `lifespan` corra — é lá
    que as migrações e o bootstrap do primeiro admin acontecem. Sem lifespan,
    não há tabelas e todos os testes falham do mesmo modo.
    """
    application = create_app(settings)
    # O `base_url` inclui o prefixo, como um browser que já está dentro de
    # `/xkmailutils`. Os testes continuam a pedir `/entrar` em vez de
    # `/xkmailutils/entrar`, e é essa concatenação — que o utilizador final
    # tem de acertar — que fica testada aqui.
    with SyncASGIClient(application, base_url=settings.base_url()) as client:
        yield client


@pytest.fixture
def conn(app: SyncASGIClient) -> Iterator[sqlite3.Connection]:
    """Ligação directa à base de dados, para preparar estado.

    Uma ligação nova, não a do pedido: os handlers abrem e fecham as suas, e
    esta serve só para o teste inspeccionar e semear dados.
    """
    connection = app.app.state.db_factory()
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture
def admin_user(conn) -> int:
    return service.create_user(conn, "admin@exemplo.pt", TEST_PASSWORD, is_admin=True)


@pytest.fixture
def normal_user(conn) -> int:
    return service.create_user(conn, "ana@exemplo.pt", TEST_PASSWORD)


# --------------------------------------------------------------------------
# Helpers de login
# --------------------------------------------------------------------------

UA_KNOWN = "Mozilla/5.0 (X11; Linux x86_64) Thunderbird/115"
UA_NEW = "Mozilla/5.0 (Macintosh) Firefox/121"


def csrf_from(client: SyncASGIClient, url: str = "/entrar") -> str:
    """Lê o token CSRF do formulário de login.

    Extrai do HTML em vez de chamar o helper interno: o teste passa a provar que
    o token está *no formulário* e não só que a função interna o sabe gerar.
    """
    body = client.get(url).text
    match = re.search(r'name="csrf_token" value="([^"]+)"', body)
    assert match, f"não encontrei csrf_token em {url}"
    return match.group(1)


def login(client: SyncASGIClient, email: str, password: str, user_agent: str = UA_KNOWN):
    """Faz login. Devolve a resposta (segue redirecções)."""
    token = csrf_from(client)
    return client.post(
        "/entrar",
        data={"email": email, "password": password, "csrf_token": token},
        headers={"user-agent": user_agent},
        follow_redirects=True,
    )


def login_with_otp(
    client: SyncASGIClient,
    conn,
    email: str,
    password: str,
    code: str,
    user_agent: str = UA_NEW,
):
    """Faz login e completa o segundo factor."""

    client.get("/entrar", headers={"user-agent": user_agent})
    token = csrf_from(client)
    client.post(
        "/entrar",
        data={"email": email, "password": password, "csrf_token": token},
        headers={"user-agent": user_agent},
        follow_redirects=False,
    )
    row = conn.execute(
        "SELECT * FROM otp_codes WHERE purpose = 'login' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row is not None, "não foi criado um desafio OTP"
    return client.post(
        "/verificar",
        data={"challenge_id": str(row["id"]), "code": code, "csrf_token": csrf_from(client)},
        headers={"user-agent": user_agent},
        follow_redirects=True,
    ), row


def latest_challenge(conn) -> sqlite3.Row | None:
    """A última linha de `otp_codes`.

    O código em claro não existe em lado nenhum depois de `issue_challenge`, e
    não pode ser reconstruído a partir do SHA-256. Os testes injectam-no por
    monkeypatch; em produção não há forma de o obter, que é o ponto.
    """
    return conn.execute("SELECT * FROM otp_codes ORDER BY id DESC LIMIT 1").fetchone()


__all__ = [
    "TEST_PASSWORD",
    "UA_KNOWN",
    "UA_NEW",
    "captured_emails",
    "csrf_from",
    "db",
    "latest_challenge",
    "login",
    "login_with_otp",
]
