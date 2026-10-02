"""Rotas das listas de destinatários.

Cada rota que age sobre uma lista passa por `service.lista_do_utilizador`, que
inclui `user_id` no `WHERE`. Nenhuma delas confia que a lista pertence a quem
pediu: trocar o número na URL tem de dar `404`, não a lista de outra pessoa.
"""

from __future__ import annotations

import sqlite3
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import Response

from .. import config, security
from ..db import transaction
from ..templates import page
from ..web import Session, csrf_is_valid, get_db, ir, require_session
from . import service

router = APIRouter(prefix="/listas")

Db = Annotated[sqlite3.Connection, Depends(get_db)]
Active = Annotated[Session, Depends(require_session)]


def _settings(request: Request) -> config.Settings:
    return request.app.state.settings


class _NaoEncontrado(Exception):
    """Lista que não é do utilizador, ou que não existe.

    A mesma resposta para as duas coisas: distinguir "não existe" de "é de
    outra pessoa" é um oráculo que diz ao atacante que o número existe.
    """


def _exige_lista(conn: sqlite3.Connection, session: Session, list_id: int) -> sqlite3.Row:
    lista = service.lista_do_utilizador(conn, session.user_id, list_id)
    if lista is None:
        raise _NaoEncontrado()
    return lista


@router.get("")
def indice(request: Request, conn: Db, session: Active) -> Response:
    settings = _settings(request)
    return page(
        request,
        "listas.html",
        {
            "listas": service.listas_do_utilizador(conn, session.user_id),
            "teto": settings.max_list_size,
            "pendentes": service.pendentes_por_utilizador(conn, session.user_id),
            "teto_pendentes": settings.max_pending_confirmations,
        },
    )


@router.post("")
def criar(
    request: Request,
    conn: Db,
    session: Active,
    csrf_token: Annotated[str, Form()] = "",
    nome: Annotated[str, Form()] = "",
) -> Response:
    if not csrf_is_valid(session, csrf_token):
        return ir(request, "/listas?erro=csrf")
    try:
        list_id = service.criar_lista(conn, session.user_id, nome)
    except service.ErroLista as erro:
        return ir(request, f"/listas?erro=lista&detalhe={_motivo(erro)}")
    return ir(request, f"/listas/{list_id}?aviso=criada")


def _motivo(erro: Exception) -> str:
    """Mensagem de negócio num query parameter, URL-encoded.

    Vai directa porque o texto é do produto — nenhuma mensagem de `ErroLista`
    contém o que o utilizador escreveu, e todas são fixas no código. Por isso
    não há reflexão a proteger: o que há é encoding, para um nome de lista com
    acentos não partir o query string.
    """
    return quote(str(erro), safe="")


@router.get("/{list_id}")
def detalhe(request: Request, conn: Db, session: Active, list_id: int) -> Response:
    try:
        lista = _exige_lista(conn, session, list_id)
    except _NaoEncontrado:
        return ir(request, "/listas?erro=lista-inexistente")
    settings = _settings(request)
    return page(
        request,
        "lista.html",
        {
            "lista": lista,
            "enderecos": service.enderecos_da_lista(conn, list_id),
            "teto": settings.max_list_size,
            "max_tentativas": service.MAX_CONFIRM_ATTEMPTS,
        },
    )


@router.post("/{list_id}/eliminar")
def eliminar(
    request: Request,
    conn: Db,
    session: Active,
    list_id: int,
    csrf_token: Annotated[str, Form()] = "",
) -> Response:
    if not csrf_is_valid(session, csrf_token):
        return ir(request, f"/listas/{list_id}?erro=csrf")
    service.eliminar_lista(conn, session.user_id, list_id)
    return ir(request, "/listas?aviso=eliminada")


@router.post("/{list_id}/enderecos")
def adicionar(
    request: Request,
    conn: Db,
    session: Active,
    list_id: int,
    csrf_token: Annotated[str, Form()] = "",
    email: Annotated[str, Form()] = "",
    nome: Annotated[str, Form()] = "",
) -> Response:
    """Adiciona **um** endereço, sempre por confirmar.

    Adicionar não envia nada. O email sai quando o utilizador pedir a
    confirmação, que é um acto separado e explícito — senão cada auto-complete
    do navegador seria um email para um endereço que ninguém pediu.
    """
    if not csrf_is_valid(session, csrf_token):
        return ir(request, f"/listas/{list_id}?erro=csrf")
    try:
        _exige_lista(conn, session, list_id)
    except _NaoEncontrado:
        return ir(request, "/listas?erro=lista-inexistente")

    settings = _settings(request)
    normalizado = security.normalise_email(email)
    if not security.is_valid_email(normalizado):
        return ir(request, f"/listas/{list_id}?erro=email")
    if service.contar_enderecos(conn, list_id) >= settings.max_list_size:
        return ir(request, f"/listas/{list_id}?erro=teto")
    # Tecto de pendentes verificado onde o pendente nasce. Ver a nota longa em
    # `service.importar_csv`: se estivesse em `pedir_confirmacao`, importar
    # endereços deixava de ser acionável e o utilizador ficava preso.
    if service.pendentes_por_utilizador(conn, session.user_id) >= (
        settings.max_pending_confirmations
    ):
        return ir(request, f"/listas/{list_id}?erro=teto")

    agora = security.iso(security.utcnow())
    try:
        with transaction(conn):
            conn.execute(
                "INSERT OR IGNORE INTO list_addresses (list_id, email, name, created_at)"
                " VALUES (?, ?, ?, ?)",
                (list_id, normalizado, (nome or "").strip(), agora),
            )
    except sqlite3.IntegrityError:
        return ir(request, f"/listas/{list_id}?erro=duplicado")
    return ir(request, f"/listas/{list_id}?aviso=endereco")


@router.post("/{list_id}/importar")
async def importar(
    request: Request,
    conn: Db,
    session: Active,
    list_id: int,
    csrf_token: Annotated[str, Form()] = "",
    ficheiro: Annotated[UploadFile, File()] = None,  # type: ignore[assignment]
) -> Response:
    """Importa um ficheiro de endereços. **Não confirma ninguém** (FR-6.5).

    A extensão não é filtreada, por decisão: uma importação rejeitada por
    extensão obriga quem tem o ficheiro certo a renomeá-lo, e o que interessa
    é o conteúdo — que o `importar_csv` valida linha a linha.
    """
    if not csrf_is_valid(session, csrf_token):
        return ir(request, f"/listas/{list_id}?erro=csrf")
    try:
        _exige_lista(conn, session, list_id)
    except _NaoEncontrado:
        return ir(request, "/listas?erro=lista-inexistente")

    if ficheiro is None or not ficheiro.filename:
        return ir(request, f"/listas/{list_id}?erro=ficheiro")

    settings = _settings(request)
    limite = settings.max_upload_bytes
    conteudo = await ficheiro.read(limite + 1)
    if len(conteudo) > limite:
        return ir(request, f"/listas/{list_id}?erro=grande")

    try:
        resultado = service.importar_csv(conn, session.user_id, list_id, conteudo, settings)
    except service.ErroLista as erro:
        return ir(request, f"/listas/{list_id}?erro=importacao&detalhe={_motivo(erro)}")

    lista = service.lista_do_utilizador(conn, session.user_id, list_id)
    return page(
        request,
        "lista.html",
        {
            "lista": lista,
            "enderecos": service.enderecos_da_lista(conn, list_id),
            "teto": settings.max_list_size,
            "max_tentativas": service.MAX_CONFIRM_ATTEMPTS,
            "importacao": resultado,
        },
        status_code=200,
    )


@router.post("/{list_id}/confirmar-pedido")
def pedir_confirmacao(
    request: Request,
    conn: Db,
    session: Active,
    list_id: int,
    csrf_token: Annotated[str, Form()] = "",
    enderecos: Annotated[str, Form()] = "",
) -> Response:
    """Envia os códigos. Só a pendentes, e com os três guardas do serviço."""
    if not csrf_is_valid(session, csrf_token):
        return ir(request, f"/listas/{list_id}?erro=csrf")
    try:
        _exige_lista(conn, session, list_id)
    except _NaoEncontrado:
        return ir(request, "/listas?erro=lista-inexistente")

    settings = _settings(request)
    ids = _ids_do_formulario(enderecos)
    if not ids:
        return ir(request, f"/listas/{list_id}?erro=selecciona")

    try:
        resultado = service.pedir_confirmacao(conn, session.user_id, settings, list_id, ids)
    except service.ErroConfirmacao:
        return ir(request, f"/listas/{list_id}?erro=confirmacao")

    soma = resultado["enviados"] + resultado["ja_confirmados"]
    soma += resultado["em_cooldown"] + resultado["excedidos"]
    return ir(request, f"/listas/{list_id}?aviso=confirmacao&n={soma}")


def _ids_do_formulario(bruto: str) -> list[int]:
    """Lê os `address_id` escolhidos num formulário de caixas.

    Filtra em vez de `int()` a arder: um campo manipulado não pode levantar
    `ValueError` e devolver um 500 a quem não fez nada de errado além de estar
    a mexer no formulário.
    """
    ids: list[int] = []
    for parte in (bruto or "").split(","):
        parte = parte.strip()
        if parte.isdigit():
            valor = int(parte)
            if valor not in ids:
                ids.append(valor)
    return ids


@router.post("/{list_id}/enderecos/{address_id}/remover")
def remover(
    request: Request,
    conn: Db,
    session: Active,
    list_id: int,
    address_id: int,
    csrf_token: Annotated[str, Form()] = "",
) -> Response:
    if not csrf_is_valid(session, csrf_token):
        return ir(request, f"/listas/{list_id}?erro=csrf")
    try:
        _exige_lista(conn, session, list_id)
    except _NaoEncontrado:
        return ir(request, "/listas?erro=lista-inexistente")
    service.remover_endereco(conn, list_id, address_id)
    return ir(request, f"/listas/{list_id}?aviso=removido")


@router.post("/{list_id}/enderecos/{address_id}/descadenciar")
def repor_inscricao(
    request: Request,
    conn: Db,
    session: Active,
    list_id: int,
    address_id: int,
    csrf_token: Annotated[str, Form()] = "",
) -> Response:
    """Repõe uma subscrição descadenciada. Só o dono da lista o pode fazer —
    a pessoa que se descadenciou tem um caminho que não passa por aqui e não
    precisa de sessão nenhuma."""
    if not csrf_is_valid(session, csrf_token):
        return ir(request, f"/listas/{list_id}?erro=csrf")
    try:
        _exige_lista(conn, session, list_id)
    except _NaoEncontrado:
        return ir(request, "/listas?erro=lista-inexistente")
    with transaction(conn):
        conn.execute(
            "UPDATE list_addresses SET unsubscribed_at = NULL WHERE id = ? AND list_id = ?",
            (address_id, list_id),
        )
    return ir(request, f"/listas/{list_id}?aviso=reposto")


@router.post("/{list_id}/confirmar")
def submeter_confirmacao(
    request: Request,
    conn: Db,
    session: Active,
    list_id: int,
    csrf_token: Annotated[str, Form()] = "",
    address_id: Annotated[int, Form()] = 0,
    codigo: Annotated[str, Form()] = "",
) -> Response:
    """Confirma a inscrição de um endereço.

    `address_id` vem do formulário **e** o endereço tem de ser da lista, por
    isso confirmar o `address_id` de outra pessoa exigiria o código dessa
    pessoa. O código é de uso único e apaga-se ao confirmar (ver `service`).
    """
    if not csrf_is_valid(session, csrf_token):
        return ir(request, f"/listas/{list_id}?erro=csrf")
    try:
        _exige_lista(conn, session, list_id)
    except _NaoEncontrado:
        return ir(request, "/listas?erro=lista-inexistente")
    try:
        service.confirmar(conn, list_id, address_id, (codigo or "").strip())
    except service.ErroConfirmacao:
        return ir(request, f"/listas/{list_id}/confirmar?erro=confirmacao&address_id={address_id}")
    return ir(request, f"/listas/{list_id}?aviso=confirmado")


@router.get("/{list_id}/confirmar")
def formulario_confirmar(
    request: Request, conn: Db, session: Active, list_id: int, address_id: int
) -> Response:
    """O formulário onde quem recebeu o código o escreve.

    Não é o dono da lista a confirmar por outra pessoa — este formulário é o
    que *o destinatário* vê no link do email, e o `token` assinado no caminho é
    o que garante que ele só confirma a si próprio.
    """
    endereco = service.endereco_da_lista(conn, list_id, address_id)
    if endereco is None or endereco["confirmed_at"] is not None:
        return ir(request, "/listas")
    lista = service.lista_do_utilizador(conn, session.user_id, list_id)
    return page(
        request,
        "confirmar.html",
        {"endereco": endereco, "lista": lista, "address_id": address_id},
    )


__all__ = ["router"]
