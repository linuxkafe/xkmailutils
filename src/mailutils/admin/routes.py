"""Rotas de administração: utilizadores e convites.

Separada de `auth/routes.py` porque as duas têm a mesma superfície — um
formulário `POST` com token — e auditorias diferentes. Um handler de admin que
fosse revisto só quando alguém olha para o ficheiro do `auth` é exactamente o
tipo de coisa que esta separação existe para tornar visível.
"""

from __future__ import annotations

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import Response

from ..auth import service
from ..templates import page
from ..web import Session, csrf_is_valid, get_db, ir, require_admin

router = APIRouter(prefix="/admin")

Db = Annotated[sqlite3.Connection, Depends(get_db)]
AdminSession = Annotated[Session, Depends(require_admin)]


@router.get("")
def dashboard(request: Request, conn: Db, session: AdminSession) -> Response:
    users = [
        {
            "id": row["id"],
            "email": row["email"],
            "admin": bool(row["is_admin"]),
            "ativo": bool(row["is_active"]),
            "criado": row["created_at"][:10],
        }
        for row in service.list_users(conn)
    ]
    invites = [
        {
            "id": row["id"],
            "email": row["email"],
            "estado": _invite_state(row),
            "expira": row["expires_at"][:16].replace("T", " "),
        }
        for row in service.list_invites(conn)
    ]
    return page(
        request,
        "admin.html",
        {"utilizadores": users, "convites": invites, "sessao": session},
    )


def _invite_state(row: sqlite3.Row) -> str:
    from .. import security

    if row["revoked_at"]:
        return "revogado"
    if row["accepted_at"]:
        return "aceite"
    if security.is_expired(row["expires_at"]):
        return "expirado"
    return "pendente"


@router.post("/convites")
def create_invite(
    request: Request,
    conn: Db,
    session: AdminSession,
    csrf_token: Annotated[str, Form()] = "",
    email: Annotated[str, Form()] = "",
) -> Response:
    if not csrf_is_valid(session, csrf_token):
        return ir(request, "/admin?erro=csrf")
    try:
        service.send_invitation(conn, request.app.state.settings, email, session.user_id)
    except ValueError as exc:
        # Chave estável, não a mensagem: o texto vive em `templates.MESSAGENS`.
        # Um `?erro=Ja%20existe...` no URL é ilegível e não se traduz.
        slug = "ja-existe" if "já existe" in str(exc).lower() else "email-invalido"
        return ir(request, f"/admin?erro={slug}")
    except Exception:
        # O convite fica gravado mas o email não saiu. Dizer ao admin que foi
        # "enviado" seria mentira; dizer que falhou obriga-o a reenviar, e o
        # token antigo nunca vai ser usado.
        return ir(request, "/admin?erro=envio")
    return ir(request, "/admin?aviso=convite-enviado")


@router.post("/convites/{invite_id}/revogar")
def revoke_invite(
    request: Request,
    conn: Db,
    session: AdminSession,
    invite_id: int,
    csrf_token: Annotated[str, Form()] = "",
) -> Response:
    if not csrf_is_valid(session, csrf_token):
        return ir(request, "/admin?erro=csrf")
    service.revoke_invite(conn, invite_id)
    return ir(request, "/admin?aviso=revogado")


@router.post("/utilizadores/{user_id}/estado")
def toggle_user(
    request: Request,
    conn: Db,
    session: AdminSession,
    user_id: int,
    csrf_token: Annotated[str, Form()] = "",
) -> Response:
    """Activa/desactiva uma conta.

    Um admin não se desactiva a si mesmo por engano: sem outro admin, a
    instalação fica sem ninguém que a faça funcionar. O erro é irreversível
    sem acesso directo à base de dados.
    """
    if not csrf_is_valid(session, csrf_token):
        return ir(request, "/admin?erro=csrf")
    if user_id == session.user_id:
        return ir(request, "/admin?erro=auto")
    row = service.get_user(conn, user_id)
    if row is None:
        return ir(request, "/admin?erro=inexistente")
    service.set_active(conn, user_id, not bool(row["is_active"]))
    return ir(request, "/admin?aviso=estado-alterado")


__all__ = ["router"]
