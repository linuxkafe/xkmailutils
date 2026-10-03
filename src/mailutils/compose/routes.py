"""Rotas do compositor.

Três coisas, e só três: mostrar o rascunho, guardar, e enviar.

O formulário de envio **não tem** um botão de "enviar sem ver o score". Não
seria uma segurança — o score é recalculado no `enviar()` de qualquer maneira —
mas seria um botão que promete ao utilizador que pode contornar o portão, e a
única forma de o portão ser acreditado é não haver caminho na interface que o
contorne. O portão é a estrutura do `service`, não um botão.
"""

from __future__ import annotations

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import Response

from .. import config
from ..lists import service as listas
from ..templates import page
from ..web import Session, csrf_is_valid, get_db, ir, require_session
from . import service

router = APIRouter(prefix="/compor")

Db = Annotated[sqlite3.Connection, Depends(get_db)]
Active = Annotated[Session, Depends(require_session)]


def _settings(request: Request) -> config.Settings:
    return request.app.state.settings


def _listas_do_utilizador(conn: sqlite3.Connection, user_id: int) -> list[sqlite3.Row]:
    """Todas as listas do utilizador, com ou sem `from` confirmado.

    Uma lista sem remetente aparece com a explicação e sem botão de envio, em
    vez de desaparecer: esconder a lista seria o utilizador perguntar onde foi
    parar a lista que acabou de criar.
    """
    return listas.listas_do_utilizador(conn, user_id)


def _listas_enviaveis(conn: sqlite3.Connection, user_id: int) -> list[sqlite3.Row]:
    """Só as listas que passam o portão do `from`.

    É uma lista separada e não um filtro dentro do template porque a diferença
    entre as duas listas **é** a informação que a interface mostra: quantas listas
    existem e quantas podem receber email.
    """
    return [
        linha
        for linha in listas.listas_do_utilizador(conn, user_id)
        if listas.lista_pode_enviar(conn, linha["id"])
    ]


@router.get("")
def compositor(request: Request, conn: Db, session: Active) -> Response:
    """A página: rascunho, lista de destino e o score do que está escrito.

    O score é calculado **aqui**, com `avaliar()`, que é a mesma função que o
    `enviar()` chama. Não há dois sítios a calcular o score — se houvesse, o
    número mostrado e o número aplicado divergiam no primeiro patch, e o
    utilizador veria um score e receberia a decisão de outro.
    """
    settings = _settings(request)
    rascunho = service.rascunho(conn, session.user_id)
    disponiveis = _listas_do_utilizador(conn, session.user_id)
    enviar_disponiveis = _listas_enviaveis(conn, session.user_id)

    avaliacao = None
    if enviar_disponiveis and (rascunho["assunto"] or rascunho["corpo"]):
        avaliacao = service.avaliar(
            conn,
            settings,
            session.user_id,
            rascunho["assunto"],
            rascunho["corpo"],
            enviar_disponiveis[0]["id"],
        )

    return page(
        request,
        "compor.html",
        {
            "rascunho": rascunho,
            "listas": disponiveis,
            "listas_enviaveis": enviar_disponiveis,
            "avaliacao": avaliacao,
            "relatorio": None,
        },
    )


@router.post("/rascunho")
def guardar(
    request: Request,
    conn: Db,
    session: Active,
    csrf_token: Annotated[str, Form()] = "",
    assunto: Annotated[str, Form()] = "",
    corpo: Annotated[str, Form()] = "",
) -> Response:
    """Guarda o rascunho. `POST` com CSRF como tudo o que escreve."""
    if not csrf_is_valid(session, csrf_token):
        return ir(request, "/compor?erro=csrf")
    service.guardar_rascunho(conn, session.user_id, assunto, corpo)
    return ir(request, "/compor?aviso=guardado")


@router.post("/enviar")
def enviar(
    request: Request,
    conn: Db,
    session: Active,
    csrf_token: Annotated[str, Form()] = "",
    assunto: Annotated[str, Form()] = "",
    corpo: Annotated[str, Form()] = "",
    list_id: Annotated[str, Form()] = "",
) -> Response:
    """Envia. **Pontua primeiro, dentro de `enviar()`**, sem excepção possível.

    A rota não decide se envia. Recebe o `list_id` do formulário, passa-o ao
    serviço, e o serviço decide — depois de pontuar. Um `if` de bloqueio aqui
    seria uma segunda implementação da política, e a segunda implementação é
    onde as políticas divergem.
    """
    if not csrf_is_valid(session, csrf_token):
        return ir(request, "/compor?erro=csrf")
    alvo = list_id.strip()
    if not alvo.isdigit():
        return ir(request, "/compor?erro=lista")
    lista_id = int(alvo)

    relatorio = service.enviar(conn, _settings(request), session.user_id, assunto, corpo, lista_id)
    # O rascunho só é guardado quando houve envio. Guardar um email bloqueado
    # como rascunho é o comportamento certo, e a interface mostra o relatório ao
    # lado do texto para se poder corrigir.
    service.guardar_rascunho(conn, session.user_id, assunto, corpo)
    return page(
        request,
        "compor.html",
        {
            "rascunho": {"assunto": assunto, "corpo": corpo, "updated_at": ""},
            "listas": _listas_do_utilizador(conn, session.user_id),
            "listas_enviaveis": _listas_enviaveis(conn, session.user_id),
            "avaliacao": None,
            "relatorio": relatorio,
        },
        status_code=200,
    )


__all__ = ["router"]
