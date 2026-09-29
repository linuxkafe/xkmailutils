"""Rotas de autenticação: login, segundo factor, logout, convites, perfil.

Cada handler faz três coisas e nada mais: validar CSRF, chamar `service.py`,
devolver HTML. Nenhuma regra de negócio vive aqui — areasonável é que um bug de
segurança se esconda num `if` dentro de um handler, e a segunda vez que isso
acontece já é um padrão.
"""

from __future__ import annotations

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response

from .. import security
from ..config import Settings
from ..templates import page
from ..web import (
    Session,
    clear_session_cookie,
    client_ip,
    close_session,
    csrf_is_valid,
    current_session,
    ensure_pre_session_csrf,
    get_db,
    get_settings,
    ir,
    open_session,
    pre_session_csrf_ok,
    request_fingerprint,
    require_session,
    revoke_all_sessions,
    set_session_cookie,
)
from . import service

router = APIRouter()

Db = Annotated[sqlite3.Connection, Depends(get_db)]
Active = Annotated[Session, Depends(require_session)]

#: Cookie de meio-desafio entre o passo 1 e o passo 2. Tem de ser `HttpOnly` e
#: de vida curta: sem ele, recarregar a página de verificação perde o desafio.
CHALLENGE_COOKIE = "mailutils_challenge"

#: Motivo de falha → chave estável de query string. O texto vive em
#: `templates.MESSAGENS`; aqui só se decide *qual* texto. Separar as duas coisas
#: é o que permite traduzir a interface sem traduzir URLs.
_ERR_SLUGS = {
    service.LOCKED_OUT: "bloqueado",
    service.INVALID_CREDENTIALS: "invalidos",
    service.ACCOUNT_INACTIVE: "inativa",
    service.INVALID_CODE: "codigo",
    service.CHALLENGE_EXHAUSTED: "excedido",
    service.EMAIL_SEND_FAILED: "envio",
    "csrf": "csrf",
    "convite": "convite",
}


@router.get("/entrar")
def login_form(request: Request, conn: Db) -> Response:
    """Se já há sessão, não faz sentido mostrar o login."""
    settings = get_settings(request)
    if current_session(conn, settings, request) is not None:
        return ir(request, "/assinatura")
    return _public_page(request, "login.html", "login")


def _public_page(
    request: Request, template: str, purpose: str, extra: dict | None = None
) -> Response:
    """Renderiza uma página sem sessão e liga-lhe um token de pré-sessão.

    O token tem de existir *antes* de renderizar, porque o template o põe num
    campo `hidden`. Gerar o cookie depois não faria o formulário carregar nada.
    """
    # `erro` e `aviso` não entram aqui: `templates.page()` já os resolve a
    # partir da query string. Passá-los em bruto sobrepunha a tradução com o
    # slug e o utilizador lia `?erro=invalidos` em vez da mensagem.
    request.state.pre_csrf = security.new_token(24)
    context: dict = {"csrf": request.state.pre_csrf}
    context.update(extra or {})
    response = page(request, template, context)
    ensure_pre_session_csrf(request, response, purpose, token=request.state.pre_csrf)
    return response


@router.post("/entrar")
def login_submit(
    request: Request,
    conn: Db,
    email: Annotated[str, Form()],
    password: Annotated[str, Form()],
    csrf_token: Annotated[str, Form()] = "",
) -> Response:
    settings = get_settings(request)
    fingerprint = request_fingerprint(request)

    # A sessão anónica ainda não existe, por isso o CSRF vem de um cookie
    # separado com token aleatório. É a única forma de proteger o formulário
    # de login sem sessão prévia.
    if not pre_session_csrf_ok(request, "login", csrf_token):
        return _login_error(request, "csrf")

    outcome = service.login(
        conn,
        settings,
        email,
        password,
        fingerprint,
        request.headers.get("user-agent", ""),
        client_ip(request),
    )

    if outcome.ok:
        return _establish_session(
            request, conn, settings, outcome.user_id, "/assinatura?aviso=bem-vindo"
        )

    if outcome.must_verify:
        response = ir(request, "/verificar")
        # Só se põe o cookie quando existe mesmo um desafio novo. O caminho do
        # cooldown devolve `challenge_id = 0`, e escrevê-lo no cookie fazia o
        # formulário de verificação mandar um desafio inexistente — que devolve
        # "código inválido" sem o utilizador ter feito nada de errado.
        if outcome.challenge_id:
            response.set_cookie(
                CHALLENGE_COOKIE,
                str(outcome.challenge_id),
                max_age=settings.otp_ttl_minutes * 60,
                httponly=True,
                samesite="lax",
                secure=settings.secure_cookies,
                path=settings.url("/verificar"),
            )
        return response

    if outcome.reason == service.CHALLENGE_SENT:
        return ir(request, "/verificar?aviso=codigo-enviado")
    return _login_error(request, outcome.reason)


@router.get("/verificar")
def verify_form(request: Request, conn: Db) -> Response:
    challenge_id = request.cookies.get(CHALLENGE_COOKIE, "")
    if not challenge_id:
        return ir(request, "/entrar?erro=convite")
    return _public_page(
        request,
        "verify.html",
        "verify",
        extra={"challenge_id": challenge_id},
    )


@router.post("/verificar")
def verify_submit(
    request: Request,
    conn: Db,
    challenge_id: Annotated[str, Form()],
    code: Annotated[str, Form()],
    csrf_token: Annotated[str, Form()] = "",
) -> Response:
    settings = get_settings(request)
    if not pre_session_csrf_ok(request, "verify", csrf_token):
        return _login_error(request, "csrf")

    try:
        challenge = int(challenge_id)
    except ValueError:
        return _login_error(request, "convite")

    outcome = service.verify_challenge(
        conn,
        settings,
        challenge,
        code.strip(),
        request_fingerprint(request),
        request.headers.get("user-agent", ""),
    )
    if not outcome.ok:
        return ir(request, f"/verificar?erro={_ERR_SLUGS.get(outcome.reason, 'codigo')}")

    response = _establish_session(
        request, conn, settings, outcome.user_id, "/assinatura?aviso=bem-vindo"
    )
    response.delete_cookie(CHALLENGE_COOKIE, path=settings.url("/verificar"))
    return response


@router.post("/sair")
def logout(request: Request, conn: Db, csrf_token: Annotated[str, Form()] = "") -> Response:
    session = current_session(conn, get_settings(request), request)
    if session is not None and csrf_is_valid(session, csrf_token):
        close_session(conn, session.token)
    response = ir(request, "/entrar?aviso=sessao-terminada")
    clear_session_cookie(response, get_settings(request))
    return response


# --------------------------------------------------------------------------
# Convites
# --------------------------------------------------------------------------


@router.get("/convite/{token}")
def invite_form(request: Request, conn: Db, token: str) -> Response:
    invite = service.get_invite_by_token(conn, token)
    if invite is None or not _invite_usable(invite):
        return page(
            request,
            "convite.html",
            {"valido": False, "erro": "convite", "csrf": ""},
            status_code=410,
        )
    return _public_page(
        request,
        "convite.html",
        "invite",
        extra={"valido": True, "token": token, "email": invite["email"]},
    )


def _invite_usable(invite: sqlite3.Row) -> bool:
    return (
        invite["revoked_at"] is None
        and invite["accepted_at"] is None
        and not security.is_expired(invite["expires_at"])
    )


@router.post("/convite/{token}")
def invite_accept(
    request: Request,
    conn: Db,
    token: str,
    password: Annotated[str, Form()],
    password2: Annotated[str, Form()],
    csrf_token: Annotated[str, Form()] = "",
) -> Response:
    if not pre_session_csrf_ok(request, "invite", csrf_token):
        return _public_page(
            request,
            "convite.html",
            "invite",
            extra={"valido": True, "token": token, "erro": "csrf"},
        )
    if password != password2:
        return page(
            request,
            "convite.html",
            {"valido": True, "token": token, "erro": "As palavras-passe não coincidem."},
            status_code=400,
        )

    ok, reason, user_id = service.accept_invite(conn, token, password)
    if not ok:
        already_gone = reason.startswith("Convite")
        return page(
            request,
            "convite.html",
            {"valido": False, "erro": "convite" if already_gone else reason},
            status_code=410 if already_gone else 400,
        )
    return _establish_session(
        request, conn, get_settings(request), user_id, "/assinatura?aviso=bem-vindo"
    )


# --------------------------------------------------------------------------
# Dispositivos
# --------------------------------------------------------------------------


@router.get("/dispositivos")
def devices_page(request: Request, conn: Db, session: Active) -> Response:
    rows = service.list_devices(conn, session.user_id)
    devices = [
        {
            "id": row["id"],
            "label": row["label"] or security.describe_device(row["user_agent"]),
            "criado": row["created_at"][:10],
            "visto": row["last_seen_at"][:16].replace("T", " "),
            "atual": row["id"] == session.device_id,
        }
        for row in rows
    ]
    return page(request, "dispositivos.html", {"dispositivos": devices})


@router.post("/dispositivos/{device_id}/revogar")
def revoke_device(
    request: Request,
    conn: Db,
    session: Active,
    device_id: int,
    csrf_token: Annotated[str, Form()] = "",
) -> Response:
    if not csrf_is_valid(session, csrf_token):
        return ir(request, "/dispositivos?erro=csrf")
    service.revoke_device(conn, session.user_id, device_id)
    return ir(request, "/dispositivos?aviso=revogado")


# --------------------------------------------------------------------------
# Perfil
# --------------------------------------------------------------------------


@router.get("/perfil")
def profile_page(request: Request, conn: Db, session: Active) -> Response:
    return page(request, "perfil.html", {})


@router.post("/perfil/password")
def change_password(
    request: Request,
    conn: Db,
    session: Active,
    current: Annotated[str, Form()],
    new: Annotated[str, Form()],
    new2: Annotated[str, Form()],
    csrf_token: Annotated[str, Form()] = "",
) -> Response:
    if not csrf_is_valid(session, csrf_token):
        return ir(request, "/perfil?erro=csrf")

    user = service.get_user(conn, session.user_id)
    if user is None or not security.verify_password(current, user["password_hash"]):
        return ir(request, "/perfil?erro=atual-incorrecta")
    if new != new2:
        return ir(request, "/perfil?erro=nao-coincidem")
    try:
        service.set_password(conn, session.user_id, new)
    except ValueError:
        return ir(request, "/perfil?erro=curta")
    # Mudar a palavra-passe invalida as outras sessões: se a conta estava
    # comprometida, trocar a senha tem de expulsar quem entrou com a antiga.
    revoke_all_sessions(conn, session.user_id)
    return ir(request, "/perfil?aviso=alterada")


# --------------------------------------------------------------------------
# Internos
# --------------------------------------------------------------------------


def _establish_session(
    request: Request,
    conn: sqlite3.Connection,
    settings: Settings,
    user_id: int,
    destino: str,
) -> RedirectResponse:
    """Abre sessão e devolve o redireccionamento já com o cookie.

    `destino` é um caminho *interno* sem prefixo; o prefixo é aplicado aqui,
    num sítio só. Passar o URL já montado seria spreading do mesmo erro por
    todos os call sites que abrem sessão.
    """
    device_id = _device_id_for(conn, request, user_id)
    token, _ = open_session(conn, settings, user_id, device_id)
    response = ir(request, destino)
    set_session_cookie(response, settings, token)
    return response


def _device_id_for(conn: sqlite3.Connection, request: Request, user_id: int) -> int:
    """Liga a sessão ao dispositivo, para que revogar o dispositivo mate a
    sessão. Se o dispositivo ainda não existir, `0` — a sessão funciona, mas
    não morre com o dispositivo. É o melhor que se consegue fazer sem forçar
    um registo."""
    row = conn.execute(
        "SELECT id FROM devices WHERE user_id = ? AND fingerprint = ?",
        (user_id, request_fingerprint(request)),
    ).fetchone()
    return int(row["id"]) if row else 0


def _login_error(request: Request, reason: str) -> Response:
    """Redirecciona para o login com a chave do erro.

    O motivo em claro nunca chega ao URL: o texto vive em
    `templates.MESSAGENS`, indexado por esta chave.
    """
    return ir(request, f"/entrar?erro={_ERR_SLUGS.get(reason, 'invalidos')}")
