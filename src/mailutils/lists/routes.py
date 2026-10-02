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
        "teto_confirmacoes": settings.max_pending_confirmations,
        "max_tentativas": service.MAX_CONFIRM_ATTEMPTS,
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
        return ir(request, f"/listas/{list_id}?erro=teto-lista")
    # Tecto de pendentes verificado onde o pendente nasce. Ver a nota longa em
    # `service.importar_csv`: se estivesse em `pedir_confirmacao`, importar
    # endereços deixava de ser acionável e o utilizador ficava preso.
    if service.pendentes_por_utilizador(conn, session.user_id) >= (
        settings.max_pending_confirmations
    ):
        return ir(request, f"/listas/{list_id}?erro=teto-confirmacoes")

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
    confirmar_imediatamente: Annotated[str, Form()] = "",
    ficheiro: Annotated[UploadFile, File()] = None,  # type: ignore[assignment]
) -> Response:
    """Importa um ficheiro de endereços.

    Por defeito, **importar não confirma ninguém** (FR-6.5). Os endereços entram
    como pendentes e o utilizador dispara a confirmação.

    Se `confirmar_imediatamente` estiver presente no formulário, os endereços são
    inseridos já com `confirmed_at` preenchido, assumindo que o operador tem
    consentimento prévio. É uma operação de operador e está desligada por defeito.

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
        confirmar = confirmar_imediatamente.lower() in ("on", "1", "true", "yes")
        resultado = service.importar_csv(conn, session.user_id, list_id, conteudo, settings, confirmar_imediatamente=confirmar)
    except service.ErroLista as erro:
        return ir(request, f"/listas/{list_id}?erro=importacao&detalhe={_motivo(erro)}")

    contexto = _contexto_lista(conn, session.user_id, list_id, settings, request)
    contexto["importacao"] = resultado
    return page(request, "lista.html", contexto, status_code=200)


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

    # M-08: a soma dizia "enviados" a quem não recebeu nada. O aviso passa a
    # descrever as quatro contagens, e a que interessa é `enviados`.
    return ir(request, f"/listas/{list_id}?aviso=confirmacao&resumo={_resumo(resultado)}")


def _resumo(resultado: dict[str, int]) -> str:
    """As quatro contagens num query parameter.

    Uma querystring transporta texto, não um dicionário. A alternativa — a
    sessão — seria estado de servidor para um número, e o dono da lista recarrega
    a página e perde-o. Serializa-se, e o template des-serializa pela
    operação inversa.

    Os números são inteiros do serviço; nada que o utilizador escreveu entra
    aqui, e por isso não há reflex a sanitizar.
    """
    return (
        f"{resultado['enviados']}-{resultado['ja_confirmados']}"
        f"-{resultado['em_cooldown']}-{resultado['excedidos']}"
    )


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
    """Repõe uma subscrição cancelada, **enviando um novo código**.

    Só o dono da lista pode disparar o pedido, mas não pode confirmar: a
    confirmação vai para quem cancelou. Antes desta correcção o POST fazia só
    `unsubscribed_at = NULL` e o endereço voltava a receber no instante, sem
    ninguém pedir — o produto a decidir por quem cancelou. (M-01)
    """
    if not csrf_is_valid(session, csrf_token):
        return ir(request, f"/listas/{list_id}?erro=csrf")
    try:
        _exige_lista(conn, session, list_id)
    except _NaoEncontrado:
        return ir(request, "/listas?erro=lista-inexistente")
    try:
        reposto = service.repor_inscricao(
            conn, _settings(request), session.user_id, list_id, address_id
        )
    except service.ErroLista:
        return ir(request, "/listas?erro=lista-inexistente")
    if not reposto:
        return ir(request, f"/listas/{list_id}?erro=reposicao")
    return ir(request, f"/listas/{list_id}?aviso=reposto")


@router.get("/{list_id}/confirmar/{address_id}")
def formulario_confirmar(
    request: Request,
    conn: Db,
    list_id: int,
    address_id: int,
    token: Annotated[str, Query()] = "",
) -> Response:
    """O formulário que **o destinatário** vê depois de clicar no email.

    Não exige sessão, porque o destinatário não tem conta nenhuma — é essa a
    razão de o link ser o que traz a identificação. A rota **não** confia no
    `address_id` do caminho: o token assinado tem de concordar com ele, ou o
    pedido é recusado. (B-04)

    Antes desta correcção a rota exigia sessão do **dono da lista** e lia o
    endereço sem verificar dono nenhum. Isto é, o dono confirmava em nome do
    destinatário e qualquer conta da instalação lia qualquer email.
    """
    settings = _settings(request)
    if not _token_valido(
        settings, web.PURPOSE_CONFIRM, list_id, address_id, token, confirmando=True
    ):
        return ir(request, "/listas?erro=token")

    endereco = service.endereco_da_lista(conn, list_id, address_id)
    if endereco is None or endereco["confirmed_at"] is not None:
        return ir(request, "/listas?erro=token")

    return page(
        request,
        "confirmar.html",
        {
            "endereco": endereco,
            "lista": service.lista_publico(conn, list_id),
            "address_id": address_id,
            "token": token,
        },
    )


@router.post("/{list_id}/confirmar/{address_id}")
def submeter_confirmacao(
    request: Request,
    conn: Db,
    list_id: int,
    address_id: int,
    token: Annotated[str, Form()] = "",
    codigo: Annotated[str, Form()] = "",
) -> Response:
    """Confirma a inscrição, com o código que foi enviado para este endereço.

    O código de 6 dígitos é o que prova que quem pede é quem recebe o email, e
    o token é o que prova que este pedido é para este endereço. Os dois são
    necessários: o código sozinho é adivinhável, o token sozinho é
    encaminhável.
    """
    settings = _settings(request)
    if not _token_valido(
        settings, web.PURPOSE_CONFIRM, list_id, address_id, token, confirmando=True
    ):
        return ir(request, "/listas?erro=token")
    try:
        service.confirmar(conn, list_id, address_id, (codigo or "").strip())
    except service.ErroConfirmacao:
        # O utilizador **é** o destinatário e não tem sessão a que voltar. Um
        # redirect para `/listas` seria perdê-lo; a mensagem vai para o query
        # string da própria página.
        return ir(
            request,
            f"/listas/{list_id}/confirmar/{address_id}?token={token}&erro=confirmacao",
        )
    return ir(request, f"/listas/{list_id}/confirmado")


@router.get("/{list_id}/confirmado")
def confirmado(request: Request, conn: Db, list_id: int) -> Response:
    """A página de depois. Não mostra a lista, só confirma que ficou feito."""
    lista = service.lista_publico(conn, list_id)
    return page(request, "confirmado.html", {"lista": lista})


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
    if not _token_valido(
        settings, web.PURPOSE_UNSUBSCRIBE, list_id, address_id, token, confirmando=False
    ):
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
    *,
    confirmando: bool,
) -> bool:
    """Verifica o token do link e o prazo certo para ele.

    O prazo de confirmação é o do OTP (10 minutos): é um código de uso único e
    breve. O de descadência é muito mais longo, porque uma pessoa que se
    descadencia no primeiro dia e se arrepende no vigésimo quinto tem de
    conseguir voltar atrás. Confundir os dois prazos é um bug de política, não de
    código.
    """
    if confirmando:
        salt = web.LINK_SALT_CONFIRM
        max_age = settings.otp_ttl_minutes * 60
    else:
        salt = web.LINK_SALT_UNSUBSCRIBE
        max_age = settings.unsubscribe_token_days * 24 * 3600
    return web.verificar_link(settings, purpose, list_id, address_id, token, salt, max_age)


__all__ = ["router"]
