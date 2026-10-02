"""Bindings HTTP↔HTML: sessão, CSRF, dependências de rota e helpers.

Vive em `web.py` e não em `main.py` para que as rotas de auth, de admin e de
assinaturas partilhem exactamente o mesmo código de sessão. Duas
implementações de CSRF são duas implementações com um bug cada.
"""

from __future__ import annotations

import hmac
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from fastapi import Request
from fastapi.responses import RedirectResponse, Response
from itsdangerous import BadSignature, URLSafeTimedSerializer

from . import security
from .config import Settings
from .db import transaction

SESSION_COOKIE = "mailutils_session"
CSRF_FIELD = "csrf_token"
THEME_COOKIE = "mailutils_theme"

#: Salts distintos para sessão e CSRF. Partilhar salt faz com que um token
#: assinado num contexto possa ser reapresentado no outro.
SESSION_SALT = "mailutils-session-v1"
CSRF_SALT = "mailutils-csrf-v1"

#: Salts dos tokens de **propósito** — os que vivem num link e não numa sessão.
#:
#: São salts separados, e não o CSRF reutilizado, por uma razão que só aparece
#: quando alguém tenta reuse: o `purpose` vai dentro do payload assinado. Um
#: token de confirmação carrega `("confirmar", address_id)` e um de descadencia
#: carrega `("descadenciar", address_id)`. Verificar o propósito errado falha,
#: porque o payload não bate — mesmo que os dois tokens usem o mesmo segredo.
LINK_SALT_CONFIRM = "mailutils-confirmar-lista-v1"
LINK_SALT_UNSUBSCRIBE = "mailutils-descadenciar-lista-v1"

#: Os dois propósitos que viajam dentro do payload assinado. Se amanhã houver um
#: terceiro link, ganha o seu valor e o seu salt — não se reutiliza um destes.
PURPOSE_CONFIRM = "confirmar"
PURPOSE_UNSUBSCRIBE = "descadenciar"


class _Redirect(Exception):
    """Interrompe o handler e devolve um redireccionamento.

    Excepção em vez de valor de retorno porque uma dependência do FastAPI não
    pode devolver uma `Response` *e* prosseguir para o handler.
    """

    def __init__(self, url: str) -> None:
        self.url = url
        super().__init__(url)


@dataclass(frozen=True)
class Session:
    """Uma sessão válida.

    `is_active` é revalidado em cada pedido, não só no login: desactivar uma
    conta tem de cortar a sessão em curso, não apenas o próximo acesso.
    """

    token: str
    user_id: int
    email: str
    is_admin: bool
    csrf_token: str
    device_id: int = 0


# --------------------------------------------------------------------------
# Dependências
# --------------------------------------------------------------------------


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_db(request: Request) -> Iterator[sqlite3.Connection]:
    """Uma ligação por pedido, fechada no fim.

    `check_same_thread=False` porque o Starlette executa handlers síncronos num
    thread pool. A ligação não é partilhada entre threads — nasce e morre
    dentro do mesmo pedido.
    """
    conn = request.app.state.db_factory()
    request.state.conn = conn
    # A sessão é resolvida aqui, e não no middleware, porque o middleware
    # corre *antes* da dependência. O `base.html` precisa dela para mostrar a
    # navegação e o token CSRF, e o template é renderizado dentro do handler —
    # portanto tem de estar pronta antes disso. Resolvê-la aqui evita também
    # uma segunda ligação por pedido só para ir buscá-la.
    request.state.session = current_session(conn, get_settings(request), request)
    try:
        yield conn
    finally:
        conn.close()


def require_session(request: Request) -> Session:
    session = current_session(request.state.conn, get_settings(request), request)
    if session is None:
        raise _Redirect(get_settings(request).url("/entrar"))
    return session


def require_admin(request: Request) -> Session:
    session = require_session(request)
    if not session.is_admin:
        raise _Redirect(get_settings(request).url("/entrar?erro=acesso"))
    return session


# --------------------------------------------------------------------------
# Cookies
# --------------------------------------------------------------------------


def set_session_cookie(response: Response, settings: Settings, token: str) -> None:
    """Cookie de sessão, com o `path` da aplicação.

    O prefixo é o que mantém a sessão fora do resto do domínio: com
    `path="/"`, este cookie seria enviado a cada outro serviço do mesmo host.
    """
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.session_days * 86400,
        httponly=True,
        samesite="lax",
        secure=settings.secure_cookies,
        path=settings.url("/"),
    )


def clear_session_cookie(response: Response, settings: Settings) -> None:
    # O `path` tem de bater exactamente com o do `set_cookie`, ou o browser
    # guarda os dois e a sessão continua viva depois de "sair".
    response.delete_cookie(SESSION_COOKIE, path=settings.url("/"))


# --------------------------------------------------------------------------
# Sessões
# --------------------------------------------------------------------------


def _csrf_serializer(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.secret_key, salt=CSRF_SALT)


# --------------------------------------------------------------------------
# Tokens de link: confirmação e descadência
# --------------------------------------------------------------------------
#
# São as duas acções que o **destinatatário** faz sem ter sessão — porque não
# tem conta. Sem sessão não há CSRF de sessão, e sem CSRF de sessão a rota teria
# de confiar no `address_id` do caminho, que é adivinhável: enumerar inteiros dá
# qualquer email da instalação.
#
# O link carrega por isso um token assinado com o segredo da instalação e um
# `max_age` igual ao do OTP. Sem o token, `address_id` não chega. Com o token de
# outra pessoa, `address_id` não chega — porque o `address_id` está dentro do
# payload assinado e não é lido do caminho.
#
# `MAILUTILS_UNSUBSCRIBE_TOKEN_DAYS` é mais longo que o OTP de propósito: uma
# pessoa que se descadencia no primeiro dia e se arrepende no vigésimo quinto tem
# de conseguir voltar atrás. Ver `NFR-17` sobre o que isto **não** garante.


def assinar_link(settings: Settings, purpose: str, list_id: int, address_id: int, salt: str) -> str:
    """Token assinado para um link de propósito, com lista e endereço dentro.

    A lista vai dentro por uma razão concreta: sem ela, um link válido da lista
    A aceitava a mesma rota apontada à lista B. Com `address_id` filtrado pela
    lista isso daria `None`, mas só por acidente do esquema — e um esquema que
    é seguro por acidente não é um esquema.
    """
    return URLSafeTimedSerializer(settings.secret_key, salt=salt).dumps(
        {"p": purpose, "l": list_id, "a": address_id}
    )


def verificar_link(
    settings: Settings,
    purpose: str,
    list_id: int,
    address_id: int,
    token: str | None,
    salt: str,
    max_age_seconds: int,
) -> bool:
    """Confere o token **e** que `address_id` é o que está assinado.

    Confere `list_id` e `address_id` assinados contra os do caminho precisamente
    porque essa é a propriedade que dá sentido ao token: sem esta comparação, um
    token válido de uma pessoa confirmaria o endereço de outra, que é o mesmo bug
    que o token veio evitar.

    Levanta `BadSignature` para qualquer falha — assinatura, propósito, prazo.
    Devolve `bool` porque nenhuma rota precisa de saber *qual* das três falhou.
    """
    if not token:
        return False
    try:
        dados = URLSafeTimedSerializer(settings.secret_key, salt=salt).loads(
            token, max_age=max_age_seconds
        )
    except BadSignature:
        return False
    return (
        isinstance(dados, dict)
        and dados.get("p") == purpose
        and dados.get("l") == list_id
        and dados.get("a") == address_id
    )


def open_session(
    conn: sqlite3.Connection, settings: Settings, user_id: int, device_id: int = 0
) -> tuple[str, str]:
    """Cria uma sessão. Devolve (token do cookie, token CSRF).

    O token do cookie é opaco e aleatório, não um payload assinado: um cookie
    revogado tem de deixar de valer imediatamente, e isso só é garantido se o
    servidor guardar o digest. O CSRF é assinado — não precisa de ser
    revogável, só tem de não ser forjável.
    """
    token = security.new_token()
    csrf_token = str(_csrf_serializer(settings).dumps(user_id))
    now = security.utcnow()
    with transaction(conn):
        conn.execute(
            "INSERT INTO sessions"
            " (token_hash, user_id, device_id, csrf_token, created_at, expires_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                security.hash_token(token),
                user_id,
                device_id or None,
                csrf_token,
                security.iso(now),
                security.iso(security.in_days(settings.session_days, now)),
            ),
        )
    return token, csrf_token


def close_session(conn: sqlite3.Connection, token: str) -> None:
    with transaction(conn):
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (security.hash_token(token),))


def revoke_all_sessions(conn: sqlite3.Connection, user_id: int) -> None:
    with transaction(conn):
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))


def current_session(
    conn: sqlite3.Connection, settings: Settings, request: Request
) -> Session | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    row = conn.execute(
        "SELECT s.*, u.email, u.is_admin, u.is_active FROM sessions s"
        " JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?",
        (security.hash_token(token),),
    ).fetchone()
    if row is None or security.is_expired(row["expires_at"]) or not row["is_active"]:
        return None
    return Session(
        token=token,
        user_id=row["user_id"],
        email=row["email"],
        is_admin=bool(row["is_admin"]),
        csrf_token=row["csrf_token"],
        device_id=row["device_id"] or 0,
    )


# --------------------------------------------------------------------------
# CSRF
# --------------------------------------------------------------------------


def csrf_is_valid(session: Session, submitted: str | None) -> bool:
    """Verifica o token CSRF em tempo constante.

    A comparação tem de ser constante mesmo quando o token é vazio: um
    `if not submitted: return False` à frente introduz um oráculo de tempo
    distinto para "sem token" e "token errado". (FR-1.7)
    """
    if not submitted or not session.csrf_token:
        return False
    return hmac.compare_digest(submitted.encode(), session.csrf_token.encode())


def form_csrf(form: Any) -> str:
    value = form.get(CSRF_FIELD, "")
    return value if isinstance(value, str) else ""


# --------------------------------------------------------------------------
# CSRF de pré-sessão
# --------------------------------------------------------------------------
#
# O login e a aceitação de convite são os dois únicos formulários que existem
# *antes* de haver sessão, e por isso não têm CSRF de sessão para validar. A
# solução é um cookie de token aleatório, com o valor também no formulário.
# É o mesmo esquema do Django, e por uma boa razão: sobrevive ao facto de o
# atacante não conseguir ler o cookie de outro origin.

PRE_SESSION_COOKIES = {
    "login": "mailutils_login_csrf",
    # A verificação é a segunda metade do login, não um formulário independente.
    # Partilha o cookie para que recarregar a página não invalide o token do
    # formulário anterior no mesmo fluxo.
    "verify": "mailutils_login_csrf",
    "invite": "mailutils_guest_csrf",
    # O botão de tema está no cabeçalho de *todas* as páginas, incluindo as
    # que não têm sessão. Sem este propósito, quem chega à página de login não
    # tinha token nenhum para mandar um `POST`, e o tema ficava inacessível a
    # toda a gente que ainda não entrou. (F-01)
    "tema": "mailutils_tema_csrf",
}


def ensure_pre_session_csrf(
    request: Request, response: Response, purpose: str, token: str | None = None
) -> str:
    """Garante que existe um token de pré-sessão e devolve-o.

    Se `token` não for dado, reutiliza o do cookie: de cada vez que o utilizador
    recarrega a página de login não se quer um token novo, senão dois
    separadores abertos um invalida o outro.
    """
    name = PRE_SESSION_COOKIES[purpose]
    value = token or request.cookies.get(name, "")
    if not _is_opaque_token(value):
        value = security.new_token(24)
    response.set_cookie(
        name,
        value,
        max_age=3600,
        httponly=True,
        samesite="lax",
        secure=get_settings(request).secure_cookies,
        path=get_settings(request).url("/"),
    )
    return value


def pre_session_csrf_ok(request: Request, purpose: str, submitted: str) -> bool:
    name = PRE_SESSION_COOKIES[purpose]
    expected = request.cookies.get(name, "")
    if not submitted or not expected:
        return False
    return hmac.compare_digest(submitted.encode(), expected.encode())


def _is_opaque_token(value: str) -> bool:
    """Um token de URL-safe base64 de 24 bytes tem 32 caracteres. Qualquer
    outra coisa é lixo num cookie e é substituída."""
    return len(value) == 32 and all(c.isalnum() or c in "-_" for c in value)


# --------------------------------------------------------------------------
# Pedido
# --------------------------------------------------------------------------


def client_ip(request: Request) -> str:
    """IP do cliente.

    `X-Forwarded-For` é controlado por quem faz o pedido. Só é lido quando o
    operador liga explicitamente a confiança em proxy — e o limitador de
    tentativas depende disto, portanto ligar mal é um bypass.
    """
    if getattr(request.app.state, "trust_proxy", False):
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else ""


def request_fingerprint(request: Request) -> str:
    """Hash do User-Agent + Accept-Language. Identidade de UX, não de segurança."""
    return security.device_fingerprint(
        request.headers.get("user-agent", ""),
        request.headers.get("accept-language", ""),
    )


def redirect(url: str, status_code: int = 303) -> RedirectResponse:
    return RedirectResponse(url=url, status_code=status_code)


def ir(request: Request, path: str) -> RedirectResponse:
    """Redirecciona para um caminho **interno**, com o prefixo aplicado.

    Funil único de propósito. Com a aplicação servida em `/xkmailutils`, um
    Um `redirect("/entrar")` espalhado por dezenas de call sites é dezenas de
    oportunidades de esquecer o prefixo — e o sintoma de esquecer é um 404 numa
    página de erro, que é a pior altura possível para descobrir que se fez mal.

    Para destinos externos usa-se `redirect()` directamente, que não toca em
    nada.
    """
    return redirect(get_settings(request).url(path))


__all__ = [
    "CSRF_FIELD",
    "SESSION_COOKIE",
    "THEME_COOKIE",
    "Session",
    "clear_session_cookie",
    "client_ip",
    "close_session",
    "csrf_is_valid",
    "current_session",
    "form_csrf",
    "get_db",
    "get_settings",
    "ir",
    "open_session",
    "redirect",
    "request_fingerprint",
    "require_admin",
    "require_session",
    "revoke_all_sessions",
    "set_session_cookie",
]
