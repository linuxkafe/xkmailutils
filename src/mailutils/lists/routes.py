"""Rotas das listas de destinatários.

Cada rota que age sobre uma lista passa por `service.lista_do_utilizador`, que
inclui `user_id` no `WHERE`. Nenhuma delas confia que a lista pertence a quem
pediu: trocar o número na URL tem de dar `404`, não a lista de outra pessoa.
"""

from __future__ import annotations

import sqlite3
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import Response

from .. import config, security, web
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
            "bloqueadas": service.contar_pendentes_de_envio(conn, session.user_id),
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
        _exige_lista(conn, session, list_id)
    except _NaoEncontrado:
        return ir(request, "/listas?erro=lista-inexistente")
    settings = _settings(request)
    return page(
        request,
        "lista.html",
        _contexto_lista(conn, session.user_id, list_id, settings, request),
    )


def _contexto_lista(
    conn: sqlite3.Connection,
    user_id: int,
    list_id: int,
    settings: config.Settings,
    request: Request,
) -> dict[str, Any]:
    """Contexto da página de detalhe, mais o que veio na query string.

    `resumo` é lido aqui e não no `page()` porque é específico desta página: o
    aviso de confirmação só existe depois de um `POST` que pediu códigos. (M-08)

    `user_id` vem explícito, e não se vai buscar aqui, porque a lista já foi
    verificada contra a sessão pela rota e voltar a procurá-la sem o
    `user_id` seria um `SELECT` com menos uma condição.
    """
    return {
        "lista": service.lista_do_utilizador(conn, user_id, list_id),
        "enderecos": service.enderecos_da_lista(conn, list_id),
        "teto": settings.max_list_size,
        "remetentes": service.remetentes_do_utilizador(conn, user_id),
        "max_tentativas": service.MAX_CONFIRM_ATTEMPTS,
        "pode_enviar": service.lista_pode_enviar(conn, list_id),
        **_resumo_do_pedido(request),
    }


def _resumo_do_pedido(request: Request) -> dict[str, int]:
    """As quatro contagens do aviso de confirmação, já como inteiros.

    A leitura do query string e a conversão acontecem **aqui** e não no
    template. Um `{% set x = y | length > 0 %}` em Jinja aplica o filtro ao
    resultado da comparação, não à comparação — o que dá `Undefined` sem erro
    visível e um `<strong></strong>` vazio no ecrã. Um template que faz
    aritmética sobre query strings é um template que tem um dia em que deixa de
    ser verdade sem ninguém ver. (M-08)
    """
    bruto = request.query_params.get("resumo", "")
    partes = bruto.split("-") if bruto else []
    valores = []
    for indice in range(4):
        try:
            valores.append(int(partes[indice]))
        except (IndexError, ValueError):
            valores.append(0)
    enviados, ja_confirmados, em_cooldown, excedidos = valores
    return {
        "enviados": enviados,
        "ja_confirmados": ja_confirmados,
        "em_cooldown": em_cooldown,
        "excedidos": excedidos,
    }


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
    """Adiciona **um** endereço, activo.

    Antes este endereço nascia por confirmar e esperava um código. Já não
    nasce assim: entra activo e o operador é quem afirma ter autorização
    (FR-6.5). Adicionar continua a não enviar nada — não há código para enviar,
    e o `from` confirmado é o único email que este produto manda a partir de uma
    lista.
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
        return ir(request, f"/listas/{list_id}?erro=teto-lista")
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
    """Importa um ficheiro de endereços, activos.

    **Não há caminho de importação que confirme alguém** (FR-6.5), e o campo
    `confirmar_imediatamente` saiu do formulário por isso: existia só para dar
    ao operador a opção de assumir o consentimento, e a decisão foi assumir sem
    perguntar. O `aviso_consentimento` que a página mostra a seguir é a
    contrapartida — quem importa está a declarar que tem autorização de quem
    importou, e o produto diz isso em vez de o pressupor em silêncio.

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

    contexto = _contexto_lista(conn, session.user_id, list_id, settings, request)
    contexto["importacao"] = resultado
    return page(request, "lista.html", contexto, status_code=200)


@router.post("/{list_id}/from")
def definir_from(
    request: Request,
    conn: Db,
    session: Active,
    list_id: int,
    csrf_token: Annotated[str, Form()] = "",
    sender_id: Annotated[str, Form()] = "",
) -> Response:
    """Escolhe o `from` da lista. Só um `from` **deste** utilizador (FR-6.10).

    Aceitar um `sender_id` alheio e recusar mais tarde, no `SELECT` de envio,
    seria tarde: a lista ficava a mostrar um `from` que não pode usar, e a
    interface a dizer que a lista está pronta. O teste de dono acontece aqui.

    Um valor vazio **solta** o `from`, e é como se recua. Escolher um `from` por
    confirmar é permitido de propósito — o operador tem de poder montar a lista
    antes de abrir o email — mas a lista não envia até ele estar confirmado, e
    a página diz isso.
    """
    if not csrf_is_valid(session, csrf_token):
        return ir(request, f"/listas/{list_id}?erro=csrf")
    try:
        _exige_lista(conn, session, list_id)
    except _NaoEncontrado:
        return ir(request, "/listas?erro=lista-inexistente")

    bruto = (sender_id or "").strip()
    escolhido: int | None = None
    if bruto:
        if not bruto.isdigit():
            return ir(request, f"/listas/{list_id}?erro=from-alheio")
        escolhido = int(bruto)

    if not service.definir_from_da_lista(conn, session.user_id, list_id, escolhido):
        # "Alheio" e "inexistente" dão a mesma resposta, de propósito: dizer qual
        # dos dois foi seria confirmar a existência do `from` de outro utilizador.
        return ir(request, f"/listas/{list_id}?erro=from-alheio")
    return ir(request, f"/listas/{list_id}?aviso=from")


@router.post("/remetentes")
def registar_remetente(
    request: Request,
    conn: Db,
    session: Active,
    csrf_token: Annotated[str, Form()] = "",
    email: Annotated[str, Form()] = "",
) -> Response:
    """Regista um `from` novo, por confirmar. Reutilizável entre listas.

    Um `from` confirmado uma vez vale para todas as listas do utilizador
    (FR-6.9). Se já existia, não é erro: é o caso normal de quem tem três listas
    do mesmo endereço.
    """
    if not csrf_is_valid(session, csrf_token):
        return ir(request, "/listas?erro=csrf")
    try:
        service.registar_remetente(conn, session.user_id, email or "")
    except service.ErroLista:
        return ir(request, "/listas?erro=email")
    return ir(request, "/listas?aviso=remetente")


@router.post("/remetentes/{sender_id}/pedir-codigo")
def pedir_codigo_remetente(
    request: Request,
    conn: Db,
    session: Active,
    sender_id: int,
    csrf_token: Annotated[str, Form()] = "",
) -> Response:
    """Manda o código do `from`. Sessão, dono, e o cooldown do serviço.

    A resposta **não** traz o código, nem num caso de erro. O serviço é quem
    envia o email; a rota só diz que foi. Um código na resposta seria um código
    que chega a quem tem sessão e clica no botão — que é exactamente quem o
    sistema de segurança não quer a poder confirmar um `from`.
    """
    if not csrf_is_valid(session, csrf_token):
        return ir(request, "/listas?erro=csrf")
    try:
        service.pedir_confirmacao_remetente(conn, session.user_id, sender_id, _settings(request))
    except service.ErroConfirmacao:
        return ir(request, "/listas?erro=codigo")
    return ir(request, "/listas?aviso=codigo")


@router.post("/remetentes/{sender_id}/confirmar")
def confirmar_remetente(
    request: Request,
    conn: Db,
    session: Active,
    sender_id: int,
    csrf_token: Annotated[str, Form()] = "",
    codigo: Annotated[str, Form()] = "",
) -> Response:
    """Confirma o `from`. O código vai por `POST` e nunca volta no URL."""
    if not csrf_is_valid(session, csrf_token):
        return ir(request, "/listas?erro=csrf")
    if service.confirmar_remetente(conn, session.user_id, sender_id, (codigo or "").strip()):
        return ir(request, "/listas?aviso=confirmado")
    return ir(request, "/listas?erro=codigo")


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


@router.get("/{list_id}/descadenciar/{address_id}")
def descadenciar_link(
    request: Request,
    conn: Db,
    list_id: int,
    address_id: int,
    token: Annotated[str, Query()] = "",
) -> Response:
    """Descadência por link. Um clique, sem sessão, sem CSRF — como manda a RFC.

    Sem sessão não há CSRF de sessão para validar, e por isso é o token que
    garante que o link não pode ser forçado a alguém. `POST` e não `GET` porque
    um `GET` que muda estado é o que um scanner de linksFollow prefere.

    A porta é `POST`, e o `List-Unsubscribe` de um clique (FR-6.8) vai exigir
    este mesmo caminho. Ver o follow-up do T015.
    """
    settings = _settings(request)
    if not _token_valido(settings, web.PURPOSE_UNSUBSCRIBE, list_id, address_id, token):
        return ir(request, "/listas?erro=token")
    service.descadenciar(conn, address_id)
    return ir(request, f"/listas/{list_id}/descadenciado")


@router.get("/{list_id}/descadenciado")
def descadenciado(request: Request, conn: Db, list_id: int) -> Response:
    lista = service.lista_publico(conn, list_id)
    return page(request, "descadenciado.html", {"lista": lista})


def _token_valido(
    settings: config.Settings,
    purpose: str,
    list_id: int,
    address_id: int,
    token: str,
) -> bool:
    """Verifica o token do link de descadência.

    Este era o validador de dois prazos: o do OTP, curto, para confirmar, e o da
    descadência, longo, para se arrepender no vigésimo quinto dia. Com o
    `T017-A` só resta o segundo, e uma função com dois caminhos em que um
    morreu é um caminho morto à espera de alguém lhe chamar com o valor errado.

    O prazo é `unsubscribe_token_days`, e não o do OTP porque quem se descadencia
    no primeiro dia e se arrepende passados vinte e cinco tem de conseguir
    voltar atrás.
    """
    return web.verificar_link(
        settings,
        purpose,
        list_id,
        address_id,
        token,
        web.LINK_SALT_UNSUBSCRIBE,
        settings.unsubscribe_token_days * 24 * 3600,
    )


__all__ = ["router"]
