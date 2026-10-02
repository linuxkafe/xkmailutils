"""Rotas do compositor de email."""

from __future__ import annotations

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import JSONResponse

from mailutils import config
from mailutils.compose import service
from mailutils.templates import page
from mailutils.web import Session, get_db, ir, require_session

Db = Annotated[sqlite3.Connection, Depends(get_db)]
Active = Annotated[Session, Depends(require_session)]

def _settings(request: Request) -> config.Settings:
    return request.app.state.settings


router = APIRouter(prefix="/compose", tags=["compose"])


router = APIRouter(prefix="/compose", tags=["compose"])


@router.get("")
def editor(
    request: Request,
    conn: Db,
    session: Active,
) -> Response:
    """Mostra o editor de email."""
    # Por agora, carrega o estado vazio. Depois podemos persistir rascunhos.
    settings = _settings(request)
    # Precisamos de carregar as listas para o utilizador escolher
    listas = list(conn.execute(
        "SELECT id, name FROM recipient_lists WHERE user_id = ? ORDER BY name",
        (session.user_id,)
    ))
    
    contexto = {
        "listas": listas,
        "subject": "",
        "body": "",
        "attach_signature": True,
        "list_id": None,
    }
    return page(request, "compose.html", contexto)


@router.post("/score")
def api_score(
    request: Request,
    conn: Db,
    session: Active,
    subject: Annotated[str, Form()] = "",
    body: Annotated[str, Form()] = "",
    attach_signature: Annotated[str, Form()] = "on",
) -> JSONResponse:
    """Calcula o score de spam em tempo real (AJAX)."""
    settings = _settings(request)
    use_sig = attach_signature.lower() in ("on", "1", "true", "yes")
    
    res = service.compose_and_score(
        conn, session.user_id, subject, body, settings, attach_signature=use_sig
    )
    
    return JSONResponse(content={
        "score": res.score,
        "categoria": res.categoria_acentuada,
        "bloqueado": res.bloqueado,
        "regras": res.regras,
        "aviso": res.aviso,
    })


@router.post("/enviar")
def enviar(
    request: Request,
    conn: Db,
    session: Active,
    list_id: Annotated[int, Form()],
    subject: Annotated[str, Form()],
    body: Annotated[str, Form()],
    attach_signature: Annotated[str, Form()] = "on",
    agendar_para: Annotated[str, Form()] = "",
) -> Response:
    """Envia ou agenda o email."""
    settings = _settings(request)
    use_sig = attach_signature.lower() in ("on", "1", "true", "yes")
    
    if agendar_para:
        res = service.agendar_envio(
            conn, session.user_id, list_id, subject, body, agendar_para, settings, attach_signature=use_sig
        )
        if res.agendado:
            return ir(request, "/listas?aviso=agendado")
        else:
            return ir(request, f"/compose?erro=bloqueado&list_id={list_id}")
    
    res = service.enviar_agora(
        conn, session.user_id, list_id, subject, body, settings, attach_signature=use_sig
    )
    
    if res.enviado > 0 or res.falhado > 0:
        return ir(request, f"/listas/{list_id}?aviso=enviado&n={res.enviado}&f={res.falhado}")
    
    return ir(request, f"/compose?erro=falha&list_id={list_id}")
